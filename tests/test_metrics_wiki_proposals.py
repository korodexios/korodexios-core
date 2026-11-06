"""Metrics, the project wiki, and core proposals from draft to issue."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.cli.proposal import (
    _send,
    issue_text,
    similar_issues,
    write_proposal,
)
from korodexios_core.kernel.chassis import Chassis
from korodexios_core.kernel.cli_contract import CliRequest
from korodexios_core.kernel.metrics import MetricsError, MetricsRegistry, Sample
from korodexios_core.modules.metrics.module import METRICS_PATH, SNAPSHOT_EVENT, MetricsModule
from korodexios_core.modules.system_info.collector import host_samples
from korodexios_core.modules.wiki.pages import (
    check_pages,
    render_page,
    scan,
    search,
    valid_slug,
)

from .test_ipc import base_args, ipc_chassis

# -- the registry ---------------------------------------------------------------------


def test_counters_gauges_summaries_and_collectors_render_as_prometheus() -> None:
    registry = MetricsRegistry()
    orders = registry.counter("shop_orders_total", "Orders taken.")
    orders.inc()
    orders.inc(2, channel="web")
    assert registry.counter("shop_orders_total") is orders
    temperature = registry.gauge("room_temperature_celsius", "Room temperature.")
    temperature.set(21.5, room='kit"chen')
    temperature.inc(1, room='kit"chen')
    temperature.dec(0.5, room='kit"chen')
    latency = registry.summary("job_seconds", "Job time.")
    latency.observe(0.25)
    with latency.time():
        pass
    registry.collector("probe", lambda: [Sample("probe_up", 1.0, {"a": "b"}, "gauge", "Up.")])
    registry.collector("broken", lambda: (_ for _ in ()).throw(OSError("sensor gone")))
    text = registry.render_prometheus()
    assert "# TYPE shop_orders_total counter" in text
    assert 'shop_orders_total{channel="web"} 2' in text and "shop_orders_total 1" in text
    assert 'room_temperature_celsius{room="kit\\"chen"} 22' in text
    assert "# TYPE job_seconds summary" in text and "job_seconds_count 2" in text
    assert 'probe_up{a="b"} 1' in text
    assert "kdx_metrics_collector_errors 1" in text
    view = registry.as_dict()
    assert view["values"]["shop_orders_total{channel=web}"] == 2
    assert orders.value(channel="web") == 2
    assert registry.remove_collector("broken") and not registry.remove_collector("broken")
    assert registry.collectors() == ("probe",)


@pytest.mark.parametrize(
    "action",
    [
        lambda r: r.counter("bad name"),
        lambda r: r.counter("x_total").inc(-1),
        lambda r: r.gauge("x_total"),
        lambda r: r.gauge("g").set(1, **{"bad-label": 1}),
        lambda r: r.gauge("g").set(1, __reserved=1),
    ],
)
def test_bad_metrics_are_refused(action: Any) -> None:
    registry = MetricsRegistry()
    registry.counter("x_total")
    with pytest.raises(MetricsError):
        action(registry)


def test_series_are_bounded_and_special_values_render() -> None:
    from korodexios_core.kernel import metrics as module

    registry = MetricsRegistry()
    gauge = registry.gauge("g")
    for index in range(module.MAX_SERIES):
        gauge.set(index, id=index)
    with pytest.raises(MetricsError):
        gauge.set(1, id="one too many")
    other = registry.gauge("h", "multi\nline")
    other.set(float("nan"), k="n")
    other.set(float("inf"), k="p")
    other.set(float("-inf"), k="m")
    text = registry.render_prometheus()
    assert 'h{k="n"} NaN' in text and "+Inf" in text and "-Inf" in text
    assert "# HELP h multi\\nline" in text


def test_host_samples_describe_this_machine(tmp_path: Path) -> None:
    names = {sample.name for sample in host_samples(tmp_path)}
    assert {"kdx_host_cpu_cores", "kdx_host_disk_total_bytes", "kdx_host_disk_free_bytes"} <= names


# -- the module on a live chassis -------------------------------------------------------


def get(base: str, path: str, token: str | None) -> tuple[int, str, str]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status), response.headers.get("Content-Type", ""), (
                response.read().decode("utf-8")
            )
    except urllib.error.HTTPError as exc:
        return int(exc.code), "", exc.read().decode("utf-8")


def test_metrics_endpoint_serves_prometheus_and_json(project_root: Path) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.start(ipc=True)
    try:
        chassis.context.metrics.counter("demo_events_total", "Demo.").inc(3)
        base = chassis.ipc.discovery.base_url()  # type: ignore[union-attr]
        token = chassis.ipc.discovery_token  # type: ignore[union-attr]
        code, kind, text = get(base, METRICS_PATH, token)
        assert code == 200 and kind.startswith("text/plain; version=0.0.4")
        for needle in ("kdx_info{", "kdx_uptime_seconds", "kdx_modules{state=\"running\"}",
                       "kdx_ipc_requests_total", "kdx_host_cpu_cores", "kdx_queue_tasks{",
                       "demo_events_total 3", "kdx_jobs_running"):
            assert needle in text, needle
        code, _kind, body = get(base, METRICS_PATH + "?format=json", token)
        assert json.loads(body)["values"]["demo_events_total"] == 3
        assert get(base, METRICS_PATH, None)[0] == 401
        module = chassis.registry.get("metrics").module  # type: ignore[union-attr]
        assert module.health()["scrapes"] == 2
    finally:
        chassis.shutdown()


def test_snapshots_are_published_when_asked_for(project_root: Path) -> None:
    chassis = ipc_chassis(project_root)
    chassis.config = chassis.config.with_cli_overrides({"modules.metrics.snapshot_interval": 5})
    chassis.context.config = chassis.config
    chassis.initialize()
    seen: list[Any] = []
    chassis.bus.subscribe(SNAPSHOT_EVENT, seen.append)
    chassis.start(ipc=False)
    try:
        module = chassis.registry.get("metrics").module  # type: ignore[union-attr]
        assert isinstance(module, MetricsModule) and module.health()["snapshot_scheduled"]
        module.publish_snapshot()
        assert seen and "values" in seen[0].payload
    finally:
        chassis.shutdown()


def test_kdx_metrics_reads_the_daemon_or_this_process(capsys, project_root: Path) -> None:
    assert main([*base_args(project_root), "metrics", "--json"]) == 0
    local = json.loads(capsys.readouterr().out)
    assert local["source"] == "this process"
    assert main([*base_args(project_root), "metrics", "--filter", "kdx_info"]) == 0
    assert "no daemon is running" in capsys.readouterr().out
    assert main([*base_args(project_root), "metrics", "--prometheus", "--filter",
                 "kdx_info"]) == 0
    assert capsys.readouterr().out.startswith("# HELP kdx_info")
    # The daemon runs in this test process; its module objects are the CLI's too, so only
    # the source is checked here - the daemon's numbers are covered over HTTP above.
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.start(ipc=True)
    try:
        assert main([*base_args(project_root), "metrics", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["source"] == "daemon"
    finally:
        chassis.shutdown()


# -- the wiki ---------------------------------------------------------------------------


def test_pages_parse_search_and_check(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "ops").mkdir(parents=True)
    (wiki / "ops" / "deploy.en.md").write_text(
        render_page("Deploying", tags=["ops"], summary="How to deploy.",
                    body="Run `kdx service install`. See [home](../home.md)."),
        encoding="utf-8",
    )
    (wiki / "ops" / "deploy.sk.md").write_text(render_page("Nasadenie"), encoding="utf-8")
    (wiki / "home.md").write_text("# Home\n\nStart [here](missing.en.md).\n", encoding="utf-8")
    (wiki / "broken.en.md").write_text("---\ntitle: [unclosed\n---\nbody\n", encoding="utf-8")
    (wiki / "open.en.md").write_text("---\ntitle: x\nno end\n", encoding="utf-8")
    (wiki / "index.md").write_text("generated\n", encoding="utf-8")
    pages = scan(wiki, "public", "en")
    by_key = {(page.slug, page.language): page for page in pages}
    assert ("ops/deploy", "en") in by_key and ("ops/deploy", "sk") in by_key
    assert ("home", "en") in by_key and by_key[("home", "en")].title == "Home"
    assert by_key[("ops/deploy", "en")].tags == ("ops",)
    hits = search(pages, "deploy service")
    assert hits and hits[0][0].slug == "ops/deploy" and hits[0][2]
    assert search(pages, "   ") == []
    issues = check_pages(pages, ["en", "sk"], index_current=False)
    messages = [issue.message for issue in issues]
    assert any("broken link to missing.en.md" in item for item in messages)
    assert not any("../home.md" in item for item in messages)
    assert any("no sk version" in item for item in messages)
    assert any("not YAML" in item for item in messages)
    assert any("no closing" in item for item in messages)
    assert any("stale" in item for item in messages)
    assert valid_slug("ops/deploy") and not valid_slug("Ops/Deploy") and not valid_slug("a/b/c/d")
    assert scan(tmp_path / "nothing", "public", "en") == []


def run(root: Path, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main([*base_args(root), *args])
    return code, capsys.readouterr().out


def test_wiki_commands_end_to_end(capsys, project_root: Path) -> None:
    (project_root / "kdx.yml").write_text(
        (project_root / "kdx.yml").read_text(encoding="utf-8")
        + "  wiki:\n    languages: [en, sk]\n",
        encoding="utf-8",
    )
    code, out = run(project_root, capsys, "wiki", "list")
    assert code == 0 and "empty" in out
    subprocess.run(["git", "init", "-q", str(project_root)], check=False)
    code, out = run(project_root, capsys, "wiki", "init")
    assert code == 0 and "created" in out
    gitignore = project_root / ".gitignore"
    if gitignore.is_file():
        assert "/docs-dev/wiki/" in gitignore.read_text(encoding="utf-8")
    assert run(project_root, capsys, "wiki", "new", "Bad Name")[0] == 2
    code, out = run(project_root, capsys, "wiki", "new", "deploy", "--tags", "ops,install",
                    "--summary", "How we deploy.", "--body", "Use kdx service install.")
    assert code == 0
    assert run(project_root, capsys, "wiki", "new", "deploy")[0] == 1
    assert run(project_root, capsys, "wiki", "new", "deploy", "--lang", "sk",
               "--title", "Nasadenie")[0] == 0
    assert run(project_root, capsys, "wiki", "new", "plans", "--internal", "--json")[0] == 0
    capsys.readouterr()
    code, out = run(project_root, capsys, "wiki", "list", "--json")
    pages = json.loads(out)["pages"]
    assert {(p["slug"], p["language"], p["scope"]) for p in pages} == {
        ("deploy", "en", "public"), ("deploy", "sk", "public"), ("plans", "en", "internal")
    }
    assert "deploy" in run(project_root, capsys, "wiki", "list", "--lang", "sk")[1]
    code, out = run(project_root, capsys, "wiki", "show", "deploy", "--lang", "de")
    assert code == 0 and "no de version" in out and "Use kdx service install." in out
    code, out = run(project_root, capsys, "wiki", "show", "deploy", "--lang", "sk", "--json")
    shown = json.loads(out)
    assert shown["title"] == "Nasadenie" and shown["languages"] == ["en", "sk"]
    assert run(project_root, capsys, "wiki", "show", "nope")[0] == 1
    code, out = run(project_root, capsys, "wiki", "search", "service", "install")
    assert code == 0 and "deploy" in out
    assert "nothing found" in run(project_root, capsys, "wiki", "search", "zebra")[1]
    hits = json.loads(run(project_root, capsys, "wiki", "search", "deploy", "--json")[1])
    assert hits["hits"][0]["slug"] == "deploy"
    assert run(project_root, capsys, "wiki", "check")[0] == 0  # warnings only
    code, out = run(project_root, capsys, "wiki", "index")
    index = (project_root / "docs" / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "- [[deploy]] - How we deploy. (en, [[deploy.sk|sk]])" in index
    code, out = run(project_root, capsys, "wiki", "check", "--json")
    report = json.loads(out)
    assert report["errors"] == 0
    assert any("no sk version" in issue["message"] for issue in report["issues"])


def test_wiki_index_is_an_mcp_resource(monkeypatch, capsys, project_root: Path) -> None:
    from .test_mcp import rpc, serve

    assert main([*base_args(project_root), "wiki", "init"]) == 0
    assert main([*base_args(project_root), "wiki", "new", "home"]) == 0
    capsys.readouterr()
    replies = serve(monkeypatch, capsys, project_root, [
        rpc("resources/list", None, 1),
        rpc("resources/read", {"uri": "kdx://wiki/public"}, 2),
        rpc("tools/call", {"name": "wiki_search", "arguments": {"query": ["home"]}}, 3),
    ], "--in-process")
    uris = {item["uri"] for item in replies[0]["result"]["resources"]}
    assert {"kdx://wiki/public", "kdx://wiki/internal"} <= uris
    assert "[[home]]" in replies[1]["result"]["contents"][0]["text"]
    assert replies[2]["result"]["isError"] is False


# -- core proposals ---------------------------------------------------------------------


def test_write_proposal_fills_sections_and_exports_without_the_project(tmp_path: Path) -> None:
    number, path = write_proposal(
        tmp_path, "Histograms in metrics",
        {"problem": "Only summaries exist.", "why": "Every API project needs latency buckets.",
         "change": "Add registry.histogram(name, buckets).", "unknown": "ignored"},
    )
    assert number == "001" and path.name == "001-histograms-in-metrics.md"
    text = path.read_text(encoding="utf-8")
    assert "## Problem\nOnly summaries exist.\n\n## Where" in text
    assert "Issue: (not sent)" in text
    title, body = issue_text(path)
    assert title == "[core-proposal] Histograms in metrics"
    assert tmp_path.name not in body and "Raised in: (not shared)" in body
    assert "Issue:" not in body and "Environment: korodexios-core" in body
    assert tmp_path.name in issue_text(path, with_project=True)[1]
    with pytest.raises(ValueError):
        write_proposal(tmp_path, "  !!  ")


class FakeGh:
    """Stands in for the GitHub CLI: records every call, answers like gh would."""

    def __init__(self, *, create_code: int = 0, listed: str = "[]") -> None:
        self.calls: list[list[str]] = []
        self.create_code = create_code
        self.listed = listed

    def __call__(self, argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if argv[1:3] == ["issue", "list"]:
            return subprocess.CompletedProcess(argv, 0, self.listed, "")
        if argv[1:3] == ["issue", "create"]:
            return subprocess.CompletedProcess(
                argv, self.create_code,
                "https://github.com/korodexios/korodexios-core/issues/42\n" if not
                self.create_code else "", "not logged in" if self.create_code else "",
            )
        raise AssertionError(argv)


def send(chassis: Chassis, gh: FakeGh | None, **options: Any) -> int:
    defaults = {"number": "1", "repo": None, "with_project": False, "open_browser": False,
                "again": False, "confirm": False}
    defaults.update(options)
    request = CliRequest(
        context=chassis.context, command="proposal send",
        namespace=argparse.Namespace(**defaults),
        options={"chassis": chassis, "runner": gh, "gh_path": "gh" if gh else ""},
    )
    return _send(request)


def test_send_shows_asks_dedupes_and_records(capsys, project_root: Path, monkeypatch) -> None:
    chassis = ipc_chassis(project_root)
    chassis.context.chassis = chassis
    _number, path = write_proposal(project_root, "Histograms in metrics")
    listed = json.dumps([{"number": 7, "title": "[core-proposal] histograms", "state": "OPEN",
                          "url": "https://github.com/x/y/issues/7"}])
    gh = FakeGh(listed=listed)
    assert send(chassis, gh) == 0  # not confirmed: shown, nothing created
    out = capsys.readouterr().out
    assert "this text would be sent" in out and "#7 [OPEN]" in out and "nothing was sent" in out
    assert not any(call[1:3] == ["issue", "create"] for call in gh.calls)
    assert send(chassis, gh, confirm=True) == 0
    create = next(call for call in gh.calls if call[1:3] == ["issue", "create"])
    assert create[create.index("--repo") + 1] == "korodexios/korodexios-core"
    assert "Issue: https://github.com/korodexios/korodexios-core/issues/42" in path.read_text(
        encoding="utf-8")
    assert not path.with_suffix(".issue.md").exists()
    capsys.readouterr()
    assert send(chassis, gh, confirm=True) == 0
    assert "already sent" in capsys.readouterr().out
    assert send(chassis, FakeGh(create_code=1), confirm=True, again=True) == 1
    assert send(chassis, gh, repo="not a repo") == 2
    assert send(chassis, gh, number="99") == 1
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    assert send(chassis, None, again=True, open_browser=True) == 0
    out = capsys.readouterr().out
    assert "gh) is not installed" in out and opened and "template=core-proposal.yml" in opened[0]
    assert path.with_suffix(".issue.md").is_file()
    chassis.shutdown()


def test_similar_issues_tolerates_gh_failures() -> None:
    def failing(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    def garbage(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "not json", "")

    def raising(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("no gh")

    for runner in (failing, garbage, raising):
        assert similar_issues("gh", "a/b", "Histograms in metrics", runner=runner) == []
    assert similar_issues("gh", "a/b", "a b", runner=failing) == []


def test_proposal_cli_show_export_and_mcp_draft(monkeypatch, capsys, project_root: Path) -> None:
    from .test_mcp import rpc, serve

    replies = serve(monkeypatch, capsys, project_root, [
        rpc("tools/call", {"name": "kdx_proposal_draft", "arguments": {
            "title": "Wiki search ranks titles", "problem": "p", "change": "c", "why": "w"}}, 1),
        rpc("tools/call", {"name": "kdx_proposal_draft", "arguments": {"title": "x"}}, 2),
    ], "--in-process")
    assert replies[0]["result"]["structuredContent"]["sent"] is False
    assert "missing required" in replies[1]["result"]["content"][0]["text"]
    assert main([*base_args(project_root), "proposal", "show", "1"]) == 0
    assert "Wiki search ranks titles" in capsys.readouterr().out
    assert main([*base_args(project_root), "proposal", "show", "1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["issue"] == "(not sent)"
    assert main([*base_args(project_root), "proposal", "export", "1"]) == 0
    assert "[core-proposal] Wiki search ranks titles" in capsys.readouterr().out
    target = project_root / "out.md"
    assert main([*base_args(project_root), "proposal", "export", "1", "--output",
                 str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("[core-proposal]")
    assert main([*base_args(project_root), "proposal", "list"]) == 0
    assert "(not sent)" in capsys.readouterr().out
