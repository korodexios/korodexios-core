"""Container files for a seeded project: ``kdx init NAME --container``.

One ``Dockerfile`` and one ``compose.yaml`` serve Docker and Podman alike (``podman compose``
reads the same files). The image is multi-stage and small: dependencies are installed into a
virtual environment in a build stage, the runtime stage holds only that environment and the
project's ``kdx.yml``, runs as an unprivileged user, publishes the control plane on
``0.0.0.0`` inside the container (compose maps it to ``127.0.0.1`` on the host) and requires a
token, because a control plane reachable beyond the loopback must not be anonymous. With
``--ui react`` an extra Node stage builds ``web/`` and the image serves it at ``/app/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .init_project import ProjectTemplate

PYTHON_IMAGE = "python:3.13-slim"
NODE_STAGE = {
    "npm": ("node:22-slim", "npm install --no-audit --no-fund", "npm run build"),
    "pnpm": ("node:22-slim", "corepack enable && pnpm install", "pnpm run build"),
    "bun": ("oven/bun:1", "bun install", "bun run build"),
}
PORT = 18123


def container_files(project: ProjectTemplate) -> dict[str, str]:
    """Return the container files, keyed by project-relative path."""
    return {
        "Dockerfile": _dockerfile(project),
        "compose.yaml": _compose(project),
        ".dockerignore": _dockerignore(),
    }


def _dockerfile(project: ProjectTemplate) -> str:
    react = project.ui == "react"
    needs_git = project.chassis_source == "git"
    lines = [
        "# syntax=docker/dockerfile:1",
        f"# {project.title}: Docker and Podman build the same file.",
        f"#   docker build -t {project.distribution} .",
        "#   KDX_TOKEN=<long random string> docker compose up -d     (see compose.yaml)",
        "",
    ]
    if react:
        image, install, build = NODE_STAGE[project.pm]
        lines += [
            f"FROM {image} AS web",
            "WORKDIR /web",
            "COPY web/package.json ./",
            f"RUN {install}",
            "COPY web ./",
            f"RUN {build}",
            "",
        ]
    lines += [f"FROM {PYTHON_IMAGE} AS build"]
    if needs_git:
        lines += [
            "# the chassis is installed from its git repository",
            "RUN apt-get update \\",
            " && apt-get install -y --no-install-recommends git \\",
            " && rm -rf /var/lib/apt/lists/*",
        ]
    lines += [
        "WORKDIR /src",
        "COPY pyproject.toml README.md ./",
        "COPY src ./src",
        "RUN python -m venv /opt/venv \\",
        " && /opt/venv/bin/pip install --no-cache-dir .",
        "",
        f"FROM {PYTHON_IMAGE}",
        "RUN useradd --system --uid 10001 --create-home kdx",
        "COPY --from=build /opt/venv /opt/venv",
        "ENV PATH=/opt/venv/bin:$PATH \\",
        "    PYTHONUNBUFFERED=1 \\",
        "    KDX_HOME=/app \\",
        "    KDX_CORE__IPC__HOST=0.0.0.0 \\",
        f"    KDX_CORE__IPC__PORT={PORT}",
        "# (kdx.yml has port 0 - ephemeral - which a published port cannot use)",
        "WORKDIR /app",
        "COPY kdx.yml ./kdx.yml",
        "# kdx.yml discovers the project's modules under src/<package>/modules at the root",
        "COPY src ./src",
    ]
    if react:
        lines += ["COPY --from=web /web/dist ./web/dist"]
    lines += [
        "RUN mkdir -p /app/var /app/data \\",
        " && chown -R kdx:kdx /app",
        "USER kdx",
        f"EXPOSE {PORT}",
        'VOLUME ["/app/var", "/app/data"]',
        "# /health needs no token; the check runs inside the container",
        "HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \\",
        f'  CMD python -c "import urllib.request; '
        f"urllib.request.urlopen('http://127.0.0.1:{PORT}/health', timeout=4)\"",
        f'CMD ["{project.script}", "run"]',
        "",
    ]
    return "\n".join(lines)


def _compose(project: ProjectTemplate) -> str:
    return "\n".join(
        [
            f"# {project.title}: docker compose up -d   (or: podman compose up -d)",
            "# KDX_TOKEN is required: the control plane listens on all interfaces inside the",
            "# container, so it must not be anonymous. Put it in a .env file next to this one",
            "# (never commit that file):  KDX_TOKEN=<long random string>",
            "services:",
            "  app:",
            "    build: .",
            f"    image: {project.distribution}:latest",
            "    restart: unless-stopped",
            "    ports:",
            f'      - "127.0.0.1:${{KDX_PORT:-{PORT}}}:{PORT}"   # KDX_PORT picks the host port',
            "      # widen the bind only behind TLS and a firewall",
            "    environment:",
            '      KDX_CORE__IPC__TOKEN: "${KDX_TOKEN:?set KDX_TOKEN to a long random string}"',
            "    volumes:",
            "      - app-var:/app/var     # logs, chassis.ipc, locks",
            "      - app-data:/app/data   # durable state: databases, queue, GUI preferences",
            "    cap_drop: [ALL]",
            "    security_opt:",
            "      - no-new-privileges:true",
            "volumes:",
            "  app-var: {}",
            "  app-data: {}",
            "",
        ]
    )


def _dockerignore() -> str:
    return "\n".join(
        [
            ".git",
            ".venv",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "var/",
            "data/",
            ".env",
            ".env.local",
            "kdx.local.*",
            "docs-dev/",
            "node_modules/",
            "web/node_modules/",
            "web/dist/",
            "web/test-results/",
            "web/playwright-report/",
            "",
        ]
    )


__all__ = ["container_files"]
