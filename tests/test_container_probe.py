"""Reading ids and engines, and proposing - never changing - a rootless id range."""

from __future__ import annotations

import json
from pathlib import Path

from korodexios_core.cli.entry import main
from korodexios_core.cli.init_project import build_files, project_from_inputs
from korodexios_core.kernel.container_probe import (
    parse_id_map,
    parse_subids,
    passwd_ids,
    probe_container_host,
    propose_range,
)

from .test_ipc import base_args

PASSWD = (
    "root:x:0:0::/root:/bin/bash\n"
    "kleo:x:1000:1000::/home/kleo:/bin/bash\n"
    "nobody:x:65534:65534::/:/x\n"
)
GROUP = "root:x:0:\nkleo:x:1000:\nnogroup:x:65534:\n"
HOST_MAP = "         0          0 4294967295\n"
LXC_MAP = "         0     100000      65536\n"


def files(**contents: str):  # type: ignore[no-untyped-def]
    table = {"/proc/self/uid_map": HOST_MAP, "/etc/passwd": PASSWD, "/etc/group": GROUP,
             "/etc/subuid": "", "/etc/subgid": ""}
    table.update({key.replace("__", "/"): value for key, value in contents.items()})
    return lambda path: table.get(path)


def probe(read, *, engines=("podman",), ids=(1000, 1000), user="kleo"):  # type: ignore[no-untyped-def]
    return probe_container_host(
        read=read,
        which=lambda name: f"/bin/{name}" if name in engines else None,
        environ={}, platform="linux", ids=ids, user=user,
    )


def test_parsers_ignore_junk_and_read_the_real_formats() -> None:
    assert parse_id_map(LXC_MAP) == [(0, 65536)]
    assert parse_id_map("garbage\n0 0\n") == [] and parse_id_map(None) == []
    assert parse_subids("kleo:100000:65536\nbad\nsvc:x:1\n") == [("kleo", 100000, 65536)]
    assert passwd_ids(PASSWD) == {0: "root", 1000: "kleo", 65534: "nobody"}


def test_a_plain_host_with_a_normal_range_is_ready() -> None:
    report = probe(files(**{"__etc__subuid": "kleo:100000:65536\n"}))
    assert report["rootless_ready"] is True and report["problems"] == []
    assert report["namespace"]["restricted"] is False
    assert report["user"] == {"name": "kleo", "uid": 1000, "gid": 1000, "root": False}
    assert report["container_user"] == {"uid": 1000, "gid": 1000}  # the person's own ids


def test_the_ids_are_read_not_assumed() -> None:
    report = probe(files(**{"__etc__subuid": "u:100000:65536\n"}), ids=(1234, 5678), user="u")
    assert report["container_user"] == {"uid": 1234, "gid": 5678}
    root = probe(files(), ids=(0, 0), user="root")
    assert root["rootless_ready"] is True
    assert root["container_user"] == {"uid": 10001, "gid": 10001}  # never run the stack as root


def test_an_lxc_host_with_an_unmapped_range_gets_a_free_proposal() -> None:
    read = files(**{"__proc__self__uid_map": LXC_MAP, "__etc__subuid": "kleo:100000:65536\n",
                    "__etc__subgid": "kleo:100000:65536\n"})
    report = probe(read)
    assert report["rootless_ready"] is False and report["namespace"]["restricted"] is True
    assert "outside the ids this user namespace maps" in report["problems"][0]
    advice = report["advice"]
    first, count = advice["subuid"]["first"], advice["subuid"]["count"]
    assert first >= 10000 and first + count <= 65536  # ids 1001-9999 stay free for new accounts
    assert not first <= 1000 < first + count  # the person's own uid is never inside the range
    assert not first <= 65534 < first + count
    command = advice["command"]
    assert command.startswith("sudo usermod") and "--del-subuids 100000-165535" in command
    assert f"--add-subuids {first}-{first + count - 1}" in command and command.endswith("kleo")
    assert "never edits /etc/subuid" in advice["note"]
    fallback = advice["fallback"]  # usermod refuses while the user has processes: edit the lines
    assert f"s/^kleo:100000:65536$/kleo:{first}:{count}/' /etc/subuid" in fallback
    assert "/etc/subgid" in fallback and fallback.count("sudo sed -i") == 2


def test_a_range_another_service_owns_is_never_proposed() -> None:
    read = files(**{
        "__proc__self__uid_map": LXC_MAP,
        "__etc__subuid": "svc:1001:40000\nkleo:100000:65536\n",
        "__etc__passwd": PASSWD + "svc:x:999:999::/:/x\n",
    })
    advice = probe(read)["advice"]
    first, count = advice["subuid"]["first"], advice["subuid"]["count"]
    assert first >= 41001 or first + count <= 1001  # disjoint from svc's 1001-41000
    assert not first <= 999 < first + count


def test_a_missing_entry_and_an_exhausted_namespace_are_reported() -> None:
    none = probe(files(**{"__proc__self__uid_map": HOST_MAP}))
    assert none["rootless_ready"] is False and "no entry in /etc/subuid" in none["problems"][0]
    assert none["advice"]["subuid"]["first"] >= 100000  # the useradd convention on a roomy host
    assert none["advice"]["subuid"]["count"] == 65536
    assert " --del-" not in none["advice"]["command"]  # nothing of the user's own to remove
    tiny = probe(files(**{"__proc__self__uid_map": "0 100000 500\n"}))
    assert "no free block" in " ".join(tiny["problems"]) and tiny["advice"] == {}
    assert propose_range([(0, 65536)], set(range(0, 65536, 500)), [], "x") is None


def test_docker_only_hosts_need_no_subordinate_ids() -> None:
    report = probe(files(**{"__proc__self__uid_map": LXC_MAP}), engines=("docker",))
    assert report["rootless_ready"] is True and report["engines"] == ["docker"]


def test_windows_has_no_uid() -> None:
    report = probe_container_host(read=lambda path: None, which=lambda name: None,
                                  environ={"USERNAME": "Kleo"}, platform="win32", ids=(-1, -1))
    assert report["user"]["uid"] == -1 and report["rootless_ready"] is True
    assert report["container_user"] == {"uid": 10001, "gid": 10001}


def test_the_seeded_image_takes_ids_from_build_arguments_and_reuses_existing_accounts() -> None:
    generated = build_files(project_from_inputs("demo_app", container=True))
    dockerfile, compose = generated["Dockerfile"], generated["compose.yaml"]
    assert "ARG KDX_UID=10001" in dockerfile and "USER ${KDX_UID}:${KDX_GID}" in dockerfile
    assert "getent passwd ${KDX_UID}" in dockerfile  # an id already in the image is reused
    assert "chown -R ${KDX_UID}:${KDX_GID} /app" in dockerfile
    assert 'KDX_UID: "${KDX_UID:-10001}"' in compose and 'KDX_GID: "${KDX_GID:-10001}"' in compose


def test_the_terminal_reports_and_env_never_overwrites(project_root, capsys) -> None:
    args = base_args(project_root)
    assert main([*args, "container", "doctor", "--json"]) in (0, 1)
    out = capsys.readouterr().out
    report = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert {"user", "namespace", "engines", "rootless_ready", "container_user"} <= set(report)
    assert main([*args, "container", "doctor"]) in (0, 1)
    assert "container user" in capsys.readouterr().out

    env_file = project_root / ".env"
    env_file.write_text("KDX_TOKEN=secret\nKDX_UID=4242", encoding="utf-8")  # no trailing newline
    assert main([*args, "container", "env", "--write", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):out.rindex("}") + 1])["written"] == ["KDX_GID"]
    text = env_file.read_text(encoding="utf-8")
    assert "KDX_UID=4242" in text and text.count("KDX_UID") == 1  # existing key untouched
    assert "KDX_TOKEN=secret" in text and text.count("KDX_GID=") == 1
    assert main([*args, "container", "env", "--write", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):out.rindex("}") + 1])["written"] == []
    assert main([*args, "container", "env"]) == 0
    assert "KDX_UID=" in capsys.readouterr().out
    assert Path(project_root / ".env").read_text(encoding="utf-8") == text  # second run no-op
