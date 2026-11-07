"""Container files for seeded projects (Docker and Podman) and container detection in doctor."""

from __future__ import annotations

from pathlib import Path

import yaml

from korodexios_core.cli.entry import main
from korodexios_core.cli.init_project import build_files, project_from_inputs
from korodexios_core.kernel.ui_tiers import probe_environment

from .test_ipc import base_args

INSTRUCTIONS = {"FROM", "RUN", "COPY", "ENV", "WORKDIR", "USER", "EXPOSE", "VOLUME",
                "HEALTHCHECK", "CMD", "ARG", "LABEL", "ENTRYPOINT"}


def instructions(dockerfile: str) -> list[str]:
    """Return the instruction word of every logical Dockerfile line (continuations folded)."""
    found: list[str] = []
    continued = False
    for raw in dockerfile.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not continued:
            found.append(line.split()[0])
        continued = line.endswith("\\")
    return found


def files_for(**kwargs):  # type: ignore[no-untyped-def]
    return build_files(project_from_inputs("demo_app", container=True, **kwargs))


def test_the_dockerfile_is_multistage_unprivileged_and_health_checked() -> None:
    dockerfile = files_for()["Dockerfile"]
    words = instructions(dockerfile)
    assert set(words) <= INSTRUCTIONS, set(words) - INSTRUCTIONS
    assert words[0] == "FROM" and words.count("FROM") == 2  # build stage + runtime stage
    assert "USER kdx" in dockerfile and "--uid 10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/health" in dockerfile
    assert 'CMD ["demo-app", "run"]' in dockerfile
    assert "KDX_CORE__IPC__HOST=0.0.0.0" in dockerfile
    assert "KDX_CORE__IPC__PORT=18123" in dockerfile  # kdx.yml has the ephemeral port 0
    assert "COPY src ./src" in dockerfile and "COPY kdx.yml" in dockerfile
    assert "node" not in dockerfile  # no web stage without the react tier


def test_the_chassis_source_decides_whether_the_build_needs_git() -> None:
    assert "install -y --no-install-recommends git" in files_for(chassis_source="git")["Dockerfile"]
    assert "git" not in files_for(chassis_source="index")["Dockerfile"].split("FROM")[1]


def test_the_react_tier_adds_a_node_stage_for_the_chosen_package_manager() -> None:
    npm = files_for(ui="react")["Dockerfile"]
    assert npm.count("FROM") == 3 and "FROM docker.io/library/node:22-slim AS web" in npm
    assert "npm run build" in npm and "COPY --from=web /web/dist ./web/dist" in npm
    assert "FROM docker.io/oven/bun:1 AS web" in files_for(ui="react", pm="bun")["Dockerfile"]
    pnpm = files_for(ui="react", pm="pnpm")["Dockerfile"]
    assert "corepack enable && pnpm install" in pnpm and "pnpm run build" in pnpm


def test_compose_requires_a_token_binds_the_loopback_and_keeps_state_in_volumes() -> None:
    compose = files_for()["compose.yaml"]
    data = yaml.safe_load(compose)
    app = data["services"]["app"]
    assert app["image"] == "demo-app:latest" and app["restart"] == "unless-stopped"
    assert app["environment"]["KDX_CORE__IPC__TOKEN"].startswith("${KDX_TOKEN:?")
    assert app["ports"] == ["127.0.0.1:${KDX_PORT:-18123}:18123"]
    assert app["cap_drop"] == ["ALL"] and "no-new-privileges:true" in app["security_opt"]
    assert {v.split(":")[1] for v in app["volumes"]} == {"/app/var", "/app/data"}
    assert set(data["volumes"]) == {"app-var", "app-data"}


def test_dockerignore_keeps_secrets_and_state_out_of_the_image() -> None:
    ignore = files_for()[".dockerignore"].splitlines()
    for entry in (".git", ".env", "var/", "data/", "docs-dev/", "node_modules/", ".venv"):
        assert entry in ignore, entry


def test_the_container_files_are_written_only_when_asked(tmp_path: Path) -> None:
    args = base_args(tmp_path)
    plain = tmp_path / "plain"
    assert main([*args, "init", "plain_app", "--path", str(plain), "--yes"]) == 0
    assert not (plain / "Dockerfile").exists()
    boxed = tmp_path / "boxed"
    assert main([*args, "init", "boxed_app", "--path", str(boxed), "--yes", "--container"]) == 0
    for name in ("Dockerfile", "compose.yaml", ".dockerignore"):
        assert (boxed / name).is_file(), name
    assert 'CMD ["boxed-app", "run"]' in (boxed / "Dockerfile").read_text(encoding="utf-8")


def _probe(**kwargs):  # type: ignore[no-untyped-def]
    defaults = {"which": lambda name: None, "run": lambda command: "v1", "environ": {},
                "platform": "linux", "exists": lambda path: False, "home": Path("/home/x")}
    defaults.update(kwargs)
    return probe_environment(**defaults)


def test_doctor_knows_container_engines_and_whether_it_runs_inside_one() -> None:
    assert _probe()["container"] == {"inside": "", "engines": []}
    both = _probe(which={"docker": "/bin/docker", "podman": "/bin/podman"}.get)
    assert both["container"]["engines"] == ["docker", "podman"]
    def inside(target: str):  # type: ignore[no-untyped-def]
        return lambda path: path.as_posix() == target

    assert _probe(exists=inside("/.dockerenv"))["container"]["inside"] == "docker"
    assert _probe(exists=inside("/run/.containerenv"))["container"]["inside"] == "podman"
    assert _probe(environ={"KUBERNETES_SERVICE_HOST": "10.0.0.1"})["container"]["inside"] == (
        "kubernetes")
    assert _probe(environ={"container": "lxc"})["container"]["inside"] == "lxc"


def test_every_base_image_is_fully_qualified_so_podman_can_resolve_it() -> None:
    for kwargs in ({}, {"ui": "react"}, {"ui": "react", "pm": "bun"}):
        for line in files_for(**kwargs)["Dockerfile"].splitlines():
            if line.startswith("FROM "):
                image = line.split()[1]
                assert image.startswith("docker.io/"), line
