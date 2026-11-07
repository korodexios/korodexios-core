#!/bin/sh
# Run the core's checks (ruff, mypy, pytest) inside a clean Linux distribution image.
#
#   docker run --rm -v "$PWD:/src:ro" debian:13 sh /src/scripts/distro-check.sh
#
# Works with Docker or Podman and with the distribution's own Python (apt, dnf, apk, pacman),
# or with an image that already has one (python:3.11-slim). The checks run as an unprivileged
# user, because root ignores file permissions and would hide failures. Network is needed for
# the package manager and pip.
set -eu

MIN_MINOR=11

if [ "${1:-}" != "--as-user" ]; then
    . /etc/os-release
    echo "== ${PRETTY_NAME:-unknown distribution}"
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip git make >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q python3 python3-pip git make >/dev/null
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache python3 py3-pip git make >/dev/null
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Sy --noconfirm --needed python python-pip git make >/dev/null
    elif ! command -v python3 >/dev/null 2>&1; then
        echo "no supported package manager and no python3" >&2
        exit 2
    fi
    python3 - "$MIN_MINOR" <<'PYEOF'
import sys
minor = int(sys.argv[1])
print("python", sys.version.split()[0])
if sys.version_info < (3, minor):
    raise SystemExit(f"Python 3.{minor}+ is required, found {sys.version.split()[0]}")
PYEOF
    mkdir -p /work
    (cd /src && tar cf - --exclude=.venv --exclude=var --exclude=data --exclude=dist \
        --exclude=.mypy_cache --exclude=.pytest_cache --exclude=.ruff_cache \
        --exclude=__pycache__ --exclude=.coverage .) | (cd /work && tar xf -)
    chown -R 1000:1000 /work
    export HOME=/work
    exec python3 -c 'import os; os.setgid(1000); os.setuid(1000); os.chdir("/work"); os.execv("/bin/sh", ["sh", "/work/scripts/distro-check.sh", "--as-user"])'
fi

cd /work
python3 -m venv /work/.venv-check
. /work/.venv-check/bin/activate
pip install -q --disable-pip-version-check -e '.[dev]'
echo "-- ruff";   ruff check src tests
echo "-- mypy";   mypy
echo "-- pytest"; pytest -q -p no:cacheprovider
echo "== OK"
