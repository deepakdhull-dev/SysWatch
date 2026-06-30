#!/usr/bin/env bash
# bootstrap-build-env.sh — sets up everything needed to `make build` the
# syswatch monorepo on a Debian 13+ machine: apt prerequisites, Node.js/npm,
# and uv. Idempotent — safe to rerun on the same machine.
#
# Usage: sudo ./bootstrap-build-env.sh

set -euo pipefail

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GRN}[bootstrap]${NC} $*"; }
warn() { echo -e "${YLW}[warn]${NC} $*"; }
die()  { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Must run as root. Use: sudo $0"

# ── Distro check ────────────────────────────────────────────────────────────
# Don't trust the Debian version number alone as a proxy for "has Python
# 3.13" — check the actual capability the build needs. This makes the script
# correct on Debian 13, any later Debian release, and any derivative that
# ships a new-enough python3, without hardcoding a version string anywhere.
[[ -f /etc/os-release ]] || die "/etc/os-release not found — not a recognizable Linux distro"
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
    debian) : ;;
    *) warn "ID='${ID:-unknown}' in /etc/os-release — this script targets Debian. Proceeding anyway; apt-based derivatives often work." ;;
esac
log "Detected: ${PRETTY_NAME:-unknown}"

# ── Base apt prerequisites ───────────────────────────────────────────────────
# install.sh itself calls curl/gpg/lsb_release internally without installing
# them first — a minimal/netinst image won't have these by default.
log "Installing base packages (curl, wget, gpg, git, build-essential, lsb-release)"
apt-get update -qq
apt-get install -y -qq \
    curl wget gpg git ca-certificates lsb-release build-essential

# ── Node.js + npm (frontend build) ──────────────────────────────────────────
if ! command -v node &>/dev/null; then
    log "Installing Node.js + npm"
    apt-get install -y -qq nodejs npm
else
    log "Node.js already present: $(node --version)"
fi

NODE_MAJOR="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
if [[ "${NODE_MAJOR}" -lt 18 ]]; then
    die "Node.js ${NODE_MAJOR}.x found, but Vite 5 (used by the frontend build) requires Node >=18. Install a newer Node.js manually (e.g. via nodesource) and rerun."
fi

# ── Python >=3.13 check ──────────────────────────────────────────────────────
# Both workspace packages declare requires-python = ">=3.13". Check the
# actual system python3, since that's what uv will prefer over downloading
# its own toolchain (and a fresh-toolchain download can fail on machines
# with restricted egress, as seen mid-session).
command -v python3 &>/dev/null || die "python3 not found. Install it (apt-get install python3) and rerun."
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "${PY_VER}" in
    3.13|3.1[4-9]) log "System python3 is ${PY_VER} — satisfies >=3.13" ;;
    *)
        warn "System python3 is ${PY_VER}, below the project's >=3.13 requirement."
        warn "uv sync will attempt to download a standalone Python 3.13+ toolchain instead."
        warn "If this machine has restricted internet egress (PyPI/npm only, no GitHub releases), that download will fail — install python3.13 via apt or pyenv first."
        ;;
esac

# ── uv ────────────────────────────────────────────────────────────────────
# Installed to /usr/local/bin so it's available to any user on the machine
# (root or otherwise) without per-user PATH edits — relevant since `make
# build` may be run by a different user than whoever ran this bootstrap.
if ! command -v uv &>/dev/null; then
    log "Installing uv"
    # UV_INSTALL_DIR must be set for the `sh` that runs the installer logic
    # (right side of the pipe) — setting it on `curl` (left side) does
    # nothing, since a VAR=val prefix only scopes to the single command it
    # precedes, not across a pipeline.
    curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
else
    log "uv already present: $(uv --version)"
fi

command -v uv &>/dev/null || die "uv install completed but 'uv' not found on PATH — check /usr/local/bin is in PATH"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
log "Build environment ready."
echo "  $(python3 --version)"
echo "  $(node --version), npm $(npm --version)"
echo "  $(uv --version)"
echo ""
log "Next: cd into the SysWatch repo and run: uv sync && make build"
