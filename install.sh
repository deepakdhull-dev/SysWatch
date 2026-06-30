#!/usr/bin/env bash
# install.sh — syswatch installer
# Supports two modes:
#   agent   Extract bundle.zip, install wheel, configure systemd unit
#   server  Full stack: PostgreSQL/TimescaleDB, Grafana, Prometheus,
#           Alertmanager, PKI, JWT keys, DB migrations, server wheel,
#           all systemd units
#
# Requirements (both modes):
#   - Debian 13 (Trixie) x86_64
#   - Run as root
#   - Internet access for apt/pip
#
# Usage:
#   sudo ./install.sh              # interactive mode selection
#   sudo ./install.sh agent        # non-interactive agent install (needs BUNDLE_PATH)
#   sudo ./install.sh server       # non-interactive server install

set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────
SYSWATCH_USER="syswatch"
SYSWATCH_GROUP="syswatch"
AGENT_INSTALL_DIR="/opt/syswatch-agent"
SERVER_INSTALL_DIR="/opt/syswatch-server"
# These two MUST match syswatch_agent/config.py's load_config() defaults and
# syswatch_agent/cli.py's CONFIG_PATH/CERTS_DIR — the agent daemon hardcodes
# them and takes no --config flag.
AGENT_CONFIG_PATH="/etc/syswatch/agent.yaml"
AGENT_CERTS_DIR="/etc/syswatch/certs"
SERVER_CONFIG_DIR="/etc/syswatch/server"
PKI_DIR="/etc/syswatch/pki"
LOG_DIR="/var/log/syswatch"
AGENT_VENV="${AGENT_INSTALL_DIR}/venv"
SERVER_VENV="${SERVER_INSTALL_DIR}/venv"
TIMESCALEDB_VERSION="2"
PG_VERSION="17"
PG_DB="syswatch"
PG_USER="syswatch"
GRAFANA_PORT="3000"
PROMETHEUS_PORT="9090"
ALERTMANAGER_PORT="9093"
SERVER_GRPC_PORT="50051"
SERVER_HTTP_PORT="8080"

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
NC='\033[0m'

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo -e "${GRN}[syswatch]${NC} $*"; }
warn() { echo -e "${YLW}[warn]${NC} $*"; }
die()  { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || die "Must run as root. Use: sudo $0"
}

confirm() {
    # confirm "message" → exits 0 on y/Y, 1 otherwise
    local msg="$1"
    read -r -p "${msg} [y/N] " ans
    [[ "${ans,,}" == "y" ]]
}

create_system_user() {
    if ! id "${SYSWATCH_USER}" &>/dev/null; then
        useradd --system --no-create-home --shell /usr/sbin/nologin \
            --comment "syswatch service account" "${SYSWATCH_USER}"
        log "Created system user ${SYSWATCH_USER}"
    fi
}

install_pip_package() {
    local venv="$1"
    local wheel="$2"
    "${venv}/bin/pip" install --quiet --no-deps "${wheel}"
    # Install runtime deps from PyPI (wheel's metadata declares them)
    "${venv}/bin/pip" install --quiet "${wheel}"
}

ensure_python313() {
    # Debian 13 (Trixie) ships Python 3.13 as the default python3 package,
    # so a plain apt install satisfies the project's >=3.13 requirement.
    apt-get install -y -qq python3 python3-venv python3-pip
    PYTHON313_BIN="$(command -v python3)"
    [[ -x "${PYTHON313_BIN}" ]] || die "python3 not found after install"

    PY_VER="$("${PYTHON313_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    case "${PY_VER}" in
        3.13|3.1[4-9]) : ;;
        *) die "python3 is ${PY_VER}, but syswatch requires >=3.13. Are you on Debian 13 (Trixie) or newer?" ;;
    esac
    log "Using Python ${PY_VER} at ${PYTHON313_BIN}"
}

# ── Mode selection ─────────────────────────────────────────────────────────────
require_root

MODE="${1:-}"
if [[ -z "${MODE}" ]]; then
    echo ""
    echo "syswatch installer"
    echo "  1) agent   — install telemetry agent (requires bundle.zip)"
    echo "  2) server  — install full server stack"
    echo ""
    read -r -p "Select [1/2]: " sel
    case "${sel}" in
        1) MODE="agent"  ;;
        2) MODE="server" ;;
        *) die "Invalid selection" ;;
    esac
fi

[[ "${MODE}" == "agent" || "${MODE}" == "server" ]] \
    || die "Unknown mode '${MODE}'. Use: agent | server"

log "Mode: ${MODE}"

# ══════════════════════════════════════════════════════════════════════════════
# AGENT INSTALL
# ══════════════════════════════════════════════════════════════════════════════
install_agent() {
    # ── Locate bundle ──────────────────────────────────────────────────────
    BUNDLE_PATH="${BUNDLE_PATH:-}"
    if [[ -z "${BUNDLE_PATH}" ]]; then
        read -r -p "Path to bundle.zip: " BUNDLE_PATH
    fi
    [[ -f "${BUNDLE_PATH}" ]] || die "bundle.zip not found at: ${BUNDLE_PATH}"

    # ── Locate agent wheel ─────────────────────────────────────────────────
    AGENT_WHEEL="${AGENT_WHEEL:-}"
    if [[ -z "${AGENT_WHEEL}" ]]; then
        # Check if wheel is embedded in bundle
        TMPBUNDLE=$(mktemp -d)
        trap 'rm -rf "${TMPBUNDLE}"' EXIT
        unzip -q "${BUNDLE_PATH}" -d "${TMPBUNDLE}"

        AGENT_WHEEL=$(find "${TMPBUNDLE}" -name "syswatch_agent-*.whl" | head -1)
        if [[ -z "${AGENT_WHEEL}" ]]; then
            read -r -p "Path to syswatch_agent wheel (.whl): " AGENT_WHEEL
        fi
    fi
    [[ -f "${AGENT_WHEEL}" ]] || die "Agent wheel not found at: ${AGENT_WHEEL}"

    # ── Deps ───────────────────────────────────────────────────────────────
    log "Installing system dependencies"
    apt-get update -qq
    apt-get install -y -qq curl unzip
    ensure_python313

    # ── Directories ────────────────────────────────────────────────────────
    # Paths here MUST match syswatch_agent/config.py's load_config() defaults
    # and syswatch_agent/cli.py's CONFIG_PATH/CERTS_DIR constants exactly —
    # the agent code hardcodes these, it does not take a --config flag.
    create_system_user
    install -d -m 750 -o "${SYSWATCH_USER}" -g "${SYSWATCH_GROUP}" \
        "${AGENT_INSTALL_DIR}" \
        "${AGENT_CERTS_DIR}" \
        "${LOG_DIR}"
    install -d -m 750 -o "${SYSWATCH_USER}" -g "${SYSWATCH_GROUP}" \
        "$(dirname "${AGENT_CONFIG_PATH}")"

    # ── Extract bundle ─────────────────────────────────────────────────────
    log "Extracting bundle.zip"
    TMPBUNDLE="${TMPBUNDLE:-$(mktemp -d)}"
    unzip -q "${BUNDLE_PATH}" -d "${TMPBUNDLE}" 2>/dev/null || true

    # Expected bundle layout (matches syswatch_agent/cli.py's EXPECTED_ZIP_FILES):
    #   agent.yaml
    #   ca.crt
    #   client.crt
    #   client.key
    #   syswatch_agent-*.whl  (optional, may be supplied separately)

    [[ -f "${TMPBUNDLE}/ca.crt" ]] || die "bundle.zip missing: ca.crt"
    [[ -f "${TMPBUNDLE}/client.crt" ]] || die "bundle.zip missing: client.crt"
    [[ -f "${TMPBUNDLE}/client.key" ]] || die "bundle.zip missing: client.key"
    [[ -f "${TMPBUNDLE}/agent.yaml" ]] || die "bundle.zip missing: agent.yaml"

    install -m 640 -o "${SYSWATCH_USER}" -g "${SYSWATCH_GROUP}" \
        "${TMPBUNDLE}/ca.crt" "${AGENT_CERTS_DIR}/ca.crt"
    install -m 640 -o "${SYSWATCH_USER}" -g "${SYSWATCH_GROUP}" \
        "${TMPBUNDLE}/client.crt" "${AGENT_CERTS_DIR}/client.crt"
    install -m 600 -o "${SYSWATCH_USER}" -g "${SYSWATCH_GROUP}" \
        "${TMPBUNDLE}/client.key" "${AGENT_CERTS_DIR}/client.key"
    install -m 640 -o "${SYSWATCH_USER}" -g "${SYSWATCH_GROUP}" \
        "${TMPBUNDLE}/agent.yaml" "${AGENT_CONFIG_PATH}"

    log "Certs and config placed"

    # ── Venv + wheel ───────────────────────────────────────────────────────
    log "Creating virtualenv"
    "${PYTHON313_BIN}" -m venv "${AGENT_VENV}"
    "${AGENT_VENV}/bin/pip" install --quiet --upgrade pip

    log "Installing agent wheel"
    install_pip_package "${AGENT_VENV}" "${AGENT_WHEEL}"

    chown -R "${SYSWATCH_USER}:${SYSWATCH_GROUP}" "${AGENT_INSTALL_DIR}"

    # ── Systemd unit ───────────────────────────────────────────────────────
    # syswatch-agent-daemon is the actual long-running process (main.py:main).
    # It takes no --config flag — it hardcodes /etc/syswatch/agent.yaml.
    # syswatch-agent (no -daemon suffix) is the separate management CLI
    # (start/stop/status/update/service) and must not be used as ExecStart.
    log "Installing systemd unit"
    cat > /etc/systemd/system/syswatch-agent.service <<EOF
[Unit]
Description=syswatch telemetry agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=${SYSWATCH_USER}
Group=${SYSWATCH_GROUP}
ExecStart=${AGENT_VENV}/bin/syswatch-agent-daemon
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=syswatch-agent

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${LOG_DIR}
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now syswatch-agent.service
    log "syswatch-agent enabled and started"

    echo ""
    log "Agent install complete."
    log "Status:  systemctl status syswatch-agent"
    log "Logs:    journalctl -u syswatch-agent -f"
    log "Config:  ${AGENT_CONFIG_PATH}"
    log "Certs:   ${AGENT_CERTS_DIR}/"
}

# ══════════════════════════════════════════════════════════════════════════════
# SERVER INSTALL
# ══════════════════════════════════════════════════════════════════════════════
install_server() {
    # ── Locate server wheel ────────────────────────────────────────────────
    SERVER_WHEEL="${SERVER_WHEEL:-}"
    if [[ -z "${SERVER_WHEEL}" ]]; then
        read -r -p "Path to syswatch_server wheel (.whl): " SERVER_WHEEL
    fi
    [[ -f "${SERVER_WHEEL}" ]] || die "Server wheel not found at: ${SERVER_WHEEL}"

    # ── Admin credentials ──────────────────────────────────────────────────
    ADMIN_USERNAME="${ADMIN_USERNAME:-}"
    if [[ -z "${ADMIN_USERNAME}" ]]; then
        read -r -p "Set syswatch admin username [admin]: " ADMIN_USERNAME
        ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
    fi

    ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
    if [[ -z "${ADMIN_PASSWORD}" ]]; then
        while true; do
            read -r -s -p "Set syswatch admin password: " ADMIN_PASSWORD
            echo
            read -r -s -p "Confirm password: " ADMIN_CONFIRM
            echo
            [[ "${ADMIN_PASSWORD}" == "${ADMIN_CONFIRM}" ]] && break
            warn "Passwords do not match. Retry."
        done
    fi
    [[ ${#ADMIN_PASSWORD} -ge 12 ]] \
        || die "Admin password must be at least 12 characters"

    # ── System deps ────────────────────────────────────────────────────────
    log "Installing system packages"
    apt-get update -qq
    apt-get install -y -qq \
        curl gnupg lsb-release ca-certificates \
        openssl \
        unzip wget
    ensure_python313

    # ── PostgreSQL + TimescaleDB ───────────────────────────────────────────
    _install_postgresql_timescaledb

    # ── Grafana ────────────────────────────────────────────────────────────
    _install_grafana

    # ── Prometheus ────────────────────────────────────────────────────────
    _install_prometheus

    # ── Alertmanager ──────────────────────────────────────────────────────
    _install_alertmanager

    # ── PKI: CA + server cert ──────────────────────────────────────────────
    _generate_pki

    # ── JWT keys ───────────────────────────────────────────────────────────
    _generate_jwt_keys

    # ── Directories + user ────────────────────────────────────────────────
    create_system_user
    install -d -m 750 -o "${SYSWATCH_USER}" -g "${SYSWATCH_GROUP}" \
        "${SERVER_INSTALL_DIR}" \
        "${SERVER_CONFIG_DIR}" \
        "${LOG_DIR}"

    # ── Venv + wheel ───────────────────────────────────────────────────────
    # Must happen BEFORE _write_server_config: that step hashes the admin
    # password using passlib/bcrypt, which only exists once the server
    # wheel (and its dependencies) are installed into this venv.
    log "Creating server virtualenv"
    "${PYTHON313_BIN}" -m venv "${SERVER_VENV}"
    "${SERVER_VENV}/bin/pip" install --quiet --upgrade pip

    log "Installing server wheel"
    install_pip_package "${SERVER_VENV}" "${SERVER_WHEEL}"

    chown -R "${SYSWATCH_USER}:${SYSWATCH_GROUP}" "${SERVER_INSTALL_DIR}"

    # ── Server config ──────────────────────────────────────────────────────
    _write_server_config

    # ── alembic.ini ───────────────────────────────────────────────────────
    # alembic.ini is not part of the wheel's package data (it's a project-root
    # config file, not Python package content) — it must be copied alongside
    # the install directory explicitly. cmd_migrate (syswatch-server migrate)
    # looks for it at $SYSWATCH_HOME/alembic.ini.
    ALEMBIC_INI_SRC=""
    for candidate in \
        "$(dirname "${BASH_SOURCE[0]}")/syswatch-server/alembic.ini"
    do
        [[ -f "${candidate}" ]] && ALEMBIC_INI_SRC="${candidate}"
    done
    [[ -n "${ALEMBIC_INI_SRC}" ]] \
        || die "Could not locate alembic.ini next to install.sh (expected at syswatch-server/alembic.ini)"

    install -m 640 -o "${SYSWATCH_USER}" -g "${SYSWATCH_GROUP}" \
        "${ALEMBIC_INI_SRC}" "${SERVER_INSTALL_DIR}/alembic.ini"

    # alembic.ini ships with script_location = syswatch_server/migrations, a
    # path relative to a source checkout. Once syswatch_server is pip-installed
    # into this venv, the real migrations/ directory lives inside
    # <venv>/lib/python3.X/site-packages/syswatch_server/migrations — a path
    # that depends on the exact Python minor version, so it must be resolved
    # dynamically rather than hardcoded. Patch the installed copy in place.
    MIGRATIONS_PATH=$("${SERVER_VENV}/bin/python" -c \
        "import syswatch_server, os; print(os.path.join(os.path.dirname(syswatch_server.__file__), 'migrations'))")
    [[ -d "${MIGRATIONS_PATH}" ]] \
        || die "syswatch_server.migrations not found at ${MIGRATIONS_PATH} after wheel install — wheel may be missing migrations/ as package data"

    sed -i "s#^script_location = .*#script_location = ${MIGRATIONS_PATH}#" \
        "${SERVER_INSTALL_DIR}/alembic.ini"

    log "alembic.ini installed at ${SERVER_INSTALL_DIR}/alembic.ini (script_location=${MIGRATIONS_PATH})"

    # ── Database setup ─────────────────────────────────────────────────────
    _setup_database

    # ── Alembic migrations ─────────────────────────────────────────────────
    log "Running database migrations"
    SYSWATCH_CONFIG="${SERVER_CONFIG_DIR}/config.yaml" \
        SYSWATCH_HOME="${SERVER_INSTALL_DIR}" \
        "${SERVER_VENV}/bin/syswatch-server" migrate
    log "Migrations complete"

    # ── Systemd: syswatch-server ───────────────────────────────────────────
    _install_server_unit

    # ── Configure Grafana datasource + dashboards ──────────────────────────
    _configure_grafana

    # ── Configure Prometheus scrape ────────────────────────────────────────
    _configure_prometheus

    # ── syswatch CLI shim ──────────────────────────────────────────────────
    _install_cli_shim

    # ── Enable all units ───────────────────────────────────────────────────
    log "Enabling all syswatch systemd units"
    systemctl daemon-reload
    systemctl enable --now postgresql
    systemctl enable --now grafana-server
    systemctl enable --now prometheus
    systemctl enable --now alertmanager
    systemctl enable --now syswatch-server

    # ── Post-install summary ───────────────────────────────────────────────
    SERVER_IP=$(hostname -I | awk '{print $1}')
    echo ""
    log "Server install complete."
    echo ""
    echo "  Web UI:        http://${SERVER_IP}:${SERVER_HTTP_PORT}"
    echo "  Admin login:   ${ADMIN_USERNAME} / (password set during install)"
    echo "  Grafana:       http://${SERVER_IP}:${GRAFANA_PORT}  (admin / syswatch)"
    echo "  Prometheus:    http://${SERVER_IP}:${PROMETHEUS_PORT}"
    echo "  Alertmanager:  http://${SERVER_IP}:${ALERTMANAGER_PORT}"
    echo "  gRPC:          ${SERVER_IP}:${SERVER_GRPC_PORT} (mTLS)"
    echo ""
    echo "  Config:        ${SERVER_CONFIG_DIR}/config.yaml"
    echo "  PKI:           ${PKI_DIR}/server/"
    echo "  Generate bundle: syswatch bundle --name <hostname>"
    echo ""
    log "Logs: journalctl -u syswatch-server -f"
}

# ── PostgreSQL + TimescaleDB ───────────────────────────────────────────────────
_install_postgresql_timescaledb() {
    log "Installing PostgreSQL ${PG_VERSION} + TimescaleDB ${TIMESCALEDB_VERSION}"

    # PostgreSQL official repo (PGDG) — needed because Debian's own repos ship
    # only one PostgreSQL major version per release.
    if ! apt-cache show "postgresql-${PG_VERSION}" &>/dev/null; then
        install -d /usr/share/postgresql-common/pgdg
        curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
            | gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg
        echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] \
https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
            > /etc/apt/sources.list.d/pgdg.list
        apt-get update -qq
    fi

    apt-get install -y -qq "postgresql-${PG_VERSION}"

    # TimescaleDB: Debian 13 (Trixie) ships timescaledb natively in its own
    # repos (postgresql-17-timescaledb), so no third-party repo is needed.
    # The packagecloud.io third-party repo currently fails GPG verification
    # on Trixie under apt's newer sqv-based signature checking — avoid it.
    apt-get install -y -qq "postgresql-${PG_VERSION}-timescaledb"

    # timescaledb-tune applies recommended postgresql.conf settings
    if command -v timescaledb-tune &>/dev/null; then
        timescaledb-tune --quiet --yes \
            --pg-config="/usr/lib/postgresql/${PG_VERSION}/bin/pg_config" || true
    fi

    # Tune postgresql.conf for TimescaleDB
    PG_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"
    if ! grep -q "timescaledb" "${PG_CONF}"; then
        cat >> "${PG_CONF}" <<EOF

# TimescaleDB
shared_preload_libraries = 'timescaledb'
timescaledb.telemetry_level = off
EOF
    fi

    systemctl restart postgresql
    log "PostgreSQL + TimescaleDB installed"
}

# ── Grafana ───────────────────────────────────────────────────────────────────
_install_grafana() {
    log "Installing Grafana OSS"

    if ! dpkg -l grafana &>/dev/null 2>&1; then
        install -d /etc/apt/keyrings
        curl -fsSL https://apt.grafana.com/gpg.key \
            | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
        echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] \
https://apt.grafana.com stable main" \
            > /etc/apt/sources.list.d/grafana.list
        apt-get update -qq
        apt-get install -y -qq grafana
    fi
    log "Grafana installed"
}

# ── Prometheus ────────────────────────────────────────────────────────────────
_install_prometheus() {
    log "Installing Prometheus"

    if ! command -v prometheus &>/dev/null; then
        PROM_VER="2.52.0"
        PROM_TAR="prometheus-${PROM_VER}.linux-amd64.tar.gz"
        wget -q "https://github.com/prometheus/prometheus/releases/download/v${PROM_VER}/${PROM_TAR}" \
            -O "/tmp/${PROM_TAR}"
        tar -xzf "/tmp/${PROM_TAR}" -C /tmp
        install -m 755 "/tmp/prometheus-${PROM_VER}.linux-amd64/prometheus" /usr/local/bin/prometheus
        install -m 755 "/tmp/prometheus-${PROM_VER}.linux-amd64/promtool"   /usr/local/bin/promtool
        rm -rf "/tmp/prometheus-${PROM_VER}.linux-amd64" "/tmp/${PROM_TAR}"
    fi

    install -d -m 755 /etc/prometheus /var/lib/prometheus
    if ! id prometheus &>/dev/null; then
        useradd --system --no-create-home --shell /usr/sbin/nologin --user-group prometheus \
            || die "Failed to create system user 'prometheus'"
    fi
    chown prometheus:prometheus /var/lib/prometheus

    cat > /etc/systemd/system/prometheus.service <<EOF
[Unit]
Description=Prometheus
After=network-online.target

[Service]
User=prometheus
Group=prometheus
ExecStart=/usr/local/bin/prometheus \\
    --config.file=/etc/prometheus/prometheus.yml \\
    --storage.tsdb.path=/var/lib/prometheus \\
    --storage.tsdb.retention.time=30d \\
    --web.listen-address=0.0.0.0:${PROMETHEUS_PORT}
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
    log "Prometheus installed"
}

# ── Alertmanager ──────────────────────────────────────────────────────────────
_install_alertmanager() {
    log "Installing Alertmanager"

    if ! command -v alertmanager &>/dev/null; then
        AM_VER="0.27.0"
        AM_TAR="alertmanager-${AM_VER}.linux-amd64.tar.gz"
        wget -q "https://github.com/prometheus/alertmanager/releases/download/v${AM_VER}/${AM_TAR}" \
            -O "/tmp/${AM_TAR}"
        tar -xzf "/tmp/${AM_TAR}" -C /tmp
        install -m 755 "/tmp/alertmanager-${AM_VER}.linux-amd64/alertmanager" /usr/local/bin/alertmanager
        rm -rf "/tmp/alertmanager-${AM_VER}.linux-amd64" "/tmp/${AM_TAR}"
    fi

    install -d -m 755 /etc/alertmanager /var/lib/alertmanager
    if ! id alertmanager &>/dev/null; then
        useradd --system --no-create-home --shell /usr/sbin/nologin --user-group alertmanager \
            || die "Failed to create system user 'alertmanager'"
    fi
    chown alertmanager:alertmanager /var/lib/alertmanager

    # Minimal Alertmanager config — operator customises routes/receivers post-install
    if [[ ! -f /etc/alertmanager/alertmanager.yml ]]; then
        cat > /etc/alertmanager/alertmanager.yml <<EOF
route:
  receiver: 'null'
receivers:
  - name: 'null'
inhibit_rules: []
EOF
    fi

    cat > /etc/systemd/system/alertmanager.service <<EOF
[Unit]
Description=Alertmanager
After=network-online.target

[Service]
User=alertmanager
Group=alertmanager
ExecStart=/usr/local/bin/alertmanager \\
    --config.file=/etc/alertmanager/alertmanager.yml \\
    --storage.path=/var/lib/alertmanager \\
    --web.listen-address=0.0.0.0:${ALERTMANAGER_PORT}
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
    log "Alertmanager installed"
}

# ── PKI ───────────────────────────────────────────────────────────────────────
_generate_pki() {
    log "Generating PKI (CA + server cert)"

    install -d -m 700 "${PKI_DIR}/ca" "${PKI_DIR}/server"

    # CA key + self-signed cert (10-year validity — rotate before expiry)
    if [[ ! -f "${PKI_DIR}/ca/ca.key" ]]; then
        openssl genrsa -out "${PKI_DIR}/ca/ca.key" 4096 2>/dev/null
        openssl req -new -x509 \
            -key "${PKI_DIR}/ca/ca.key" \
            -out "${PKI_DIR}/ca/ca.crt" \
            -days 3650 \
            -subj "/CN=syswatch-ca/O=syswatch" \
            2>/dev/null
        log "CA generated: ${PKI_DIR}/ca/"
    else
        warn "CA already exists at ${PKI_DIR}/ca/ — skipping regeneration"
    fi

    # Server key + CSR + cert signed by CA (2-year validity)
    if [[ ! -f "${PKI_DIR}/server/server.crt" ]]; then
        openssl genrsa -out "${PKI_DIR}/server/server.key" 4096 2>/dev/null

        SERVER_IP=$(hostname -I | awk '{print $1}')
        OPENSSL_SAN="subjectAltName=IP:${SERVER_IP},DNS:$(hostname -f),DNS:localhost"

        openssl req -new \
            -key "${PKI_DIR}/server/server.key" \
            -out "${PKI_DIR}/server/server.csr" \
            -subj "/CN=syswatch-server/O=syswatch" \
            2>/dev/null

        openssl x509 -req \
            -in "${PKI_DIR}/server/server.csr" \
            -CA "${PKI_DIR}/ca/ca.crt" \
            -CAkey "${PKI_DIR}/ca/ca.key" \
            -CAcreateserial \
            -out "${PKI_DIR}/server/server.crt" \
            -days 730 \
            -extfile <(echo "${OPENSSL_SAN}") \
            2>/dev/null

        # Copy CA cert into server dir so agents can receive it in bundle
        cp "${PKI_DIR}/ca/ca.crt" "${PKI_DIR}/server/ca.crt"
        log "Server cert generated: ${PKI_DIR}/server/"
    else
        warn "Server cert already exists at ${PKI_DIR}/server/ — skipping"
    fi

    # Secure permissions
    chmod 600 "${PKI_DIR}/ca/ca.key" "${PKI_DIR}/server/server.key"
    chown -R "${SYSWATCH_USER}:${SYSWATCH_GROUP}" "${PKI_DIR}"
}

# ── JWT keys ──────────────────────────────────────────────────────────────────
_generate_jwt_keys() {
    log "Generating JWT RS256 key pair"

    JWT_DIR="${SERVER_CONFIG_DIR}/jwt"
    install -d -m 700 "${JWT_DIR}"

    if [[ ! -f "${JWT_DIR}/jwt.key" ]]; then
        openssl genrsa -out "${JWT_DIR}/jwt.key" 4096 2>/dev/null
        openssl rsa -in "${JWT_DIR}/jwt.key" -pubout -out "${JWT_DIR}/jwt.pub" 2>/dev/null
        chmod 600 "${JWT_DIR}/jwt.key"
        chmod 644 "${JWT_DIR}/jwt.pub"
        chown -R "${SYSWATCH_USER}:${SYSWATCH_GROUP}" "${JWT_DIR}"
        log "JWT keys: ${JWT_DIR}/"
    else
        warn "JWT keys already exist — skipping"
    fi
}

# ── Database setup ─────────────────────────────────────────────────────────────
_setup_database() {
    log "Setting up PostgreSQL database"

    # Generate a random DB password if not set
    PG_PASSWORD="${PG_PASSWORD:-$(openssl rand -hex 32)}"

    # Create role + database
    sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${PG_USER}') THEN
        CREATE ROLE ${PG_USER} WITH LOGIN PASSWORD '${PG_PASSWORD}';
    END IF;
END
\$\$;

SELECT 'CREATE DATABASE ${PG_DB} OWNER ${PG_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${PG_DB}') \gexec

GRANT ALL PRIVILEGES ON DATABASE ${PG_DB} TO ${PG_USER};
SQL

    # Enable TimescaleDB extension
    sudo -u postgres psql -d "${PG_DB}" -v ON_ERROR_STOP=1 <<SQL
CREATE EXTENSION IF NOT EXISTS timescaledb;
SQL

    # Write DB password to a file readable only by syswatch user
    DB_PASS_FILE="${SERVER_CONFIG_DIR}/.db_password"
    echo -n "${PG_PASSWORD}" > "${DB_PASS_FILE}"
    chmod 600 "${DB_PASS_FILE}"
    chown "${SYSWATCH_USER}:${SYSWATCH_GROUP}" "${DB_PASS_FILE}"

    # Export for config writing
    export PG_PASSWORD
    log "Database ready: ${PG_DB}"
}

# ── Server config ─────────────────────────────────────────────────────────────
# config.yaml is templated from syswatch-server/config.example.yaml, which ships
# inside the repo (and the built wheel's package data). install.sh does NOT
# generate config.yaml from scratch — it copies the template and substitutes
# install-time values. Edit config.example.yaml to change defaults; install.sh
# only fills in secrets/paths that must be unique per install.
_write_server_config() {
    log "Writing server config from template"

    PG_PASSWORD="${PG_PASSWORD:-$(cat "${SERVER_CONFIG_DIR}/.db_password" 2>/dev/null || echo 'CHANGE_ME')}"

    # Hash admin password (bcrypt via the just-installed server venv's passlib)
    ADMIN_HASH=$("${SERVER_VENV}/bin/python" -c "
import sys
from passlib.hash import bcrypt
print(bcrypt.hash(sys.argv[1]))
" "${ADMIN_PASSWORD}" 2>/dev/null) || die "Failed to hash admin password — is passlib installed in the server venv?"

    [[ -n "${ADMIN_HASH}" ]] || die "Admin password hash generation produced empty output"

    # Locate the template: prefer repo-relative path, fall back to package data
    # installed alongside the wheel.
    TEMPLATE=""
    for candidate in \
        "$(dirname "${BASH_SOURCE[0]}")/syswatch-server/syswatch_server/config.yaml" \
        "${SERVER_VENV}/lib/python3*/site-packages/syswatch_server/config.yaml"
    do
        # shellcheck disable=SC2086
        for f in ${candidate}; do
            [[ -f "${f}" ]] && TEMPLATE="${f}" && break 2
        done
    done
    [[ -n "${TEMPLATE}" ]] || die "Could not locate config.example.yaml template"

    log "Using template: ${TEMPLATE}"

    # Substitute install-time values into the template. Values not listed here
    # (logging level, port numbers, retention, etc.) are taken as-is from the
    # template — edit config.example.yaml to change those defaults.
    sed \
        -e "s#postgresql+asyncpg://syswatch:changeme@localhost:5432/syswatch#postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@localhost:5432/${PG_DB}#" \
        -e "s#/etc/syswatch/pki/ca/ca.crt#${PKI_DIR}/ca/ca.crt#g" \
        -e "s#/etc/syswatch/pki/server/server.crt#${PKI_DIR}/server/server.crt#" \
        -e "s#/etc/syswatch/pki/server/server.key#${PKI_DIR}/server/server.key#" \
        -e "s#/etc/syswatch/pki/ca/ca.key#${PKI_DIR}/ca/ca.key#" \
        -e "s#/etc/syswatch/server/jwt/jwt.key#${SERVER_CONFIG_DIR}/jwt/jwt.key#" \
        -e "s#/etc/syswatch/server/jwt/jwt.pub#${SERVER_CONFIG_DIR}/jwt/jwt.pub#" \
        -e "s#admin_username: \"admin\"#admin_username: \"${ADMIN_USERNAME}\"#" \
        -e "s#admin_password_hash: \"\$2b\$12\$CHANGEME\"#admin_password_hash: \"${ADMIN_HASH}\"#" \
        "${TEMPLATE}" > "${SERVER_CONFIG_DIR}/config.yaml"

    chmod 640 "${SERVER_CONFIG_DIR}/config.yaml"
    chown "${SYSWATCH_USER}:${SYSWATCH_GROUP}" "${SERVER_CONFIG_DIR}/config.yaml"
    log "Config written: ${SERVER_CONFIG_DIR}/config.yaml"
}

# ── Server systemd unit ───────────────────────────────────────────────────────
_install_server_unit() {
    log "Installing syswatch-server systemd unit"

    cat > /etc/systemd/system/syswatch-server.service <<EOF
[Unit]
Description=syswatch server
After=network-online.target postgresql.service
Wants=network-online.target
Requires=postgresql.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=${SYSWATCH_USER}
Group=${SYSWATCH_GROUP}
WorkingDirectory=${SERVER_INSTALL_DIR}
Environment="SYSWATCH_CONFIG=${SERVER_CONFIG_DIR}/config.yaml"
Environment="SYSWATCH_HOME=${SERVER_INSTALL_DIR}"
# syswatch-server-daemon is the actual long-running process (main.py:main).
# syswatch-server (no -daemon suffix) is the separate management CLI
# (start/stop/status/logs/migrate/health/open) that shells out to systemctl
# and must not be used as ExecStart — it has no "serve" subcommand.
ExecStart=${SERVER_VENV}/bin/syswatch-server-daemon
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=syswatch-server

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${LOG_DIR} ${SERVER_CONFIG_DIR}
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
EOF
    log "syswatch-server unit installed"
}

# ── Grafana: datasource + dashboard ───────────────────────────────────────────
_configure_grafana() {
    log "Configuring Grafana datasource"

    # Wait for Grafana to start
    systemctl restart grafana-server
    for i in $(seq 1 20); do
        curl -sf "http://localhost:${GRAFANA_PORT}/api/health" &>/dev/null && break
        sleep 2
    done

    GRAFANA_AUTH="admin:admin"
    GRAFANA_API="http://localhost:${GRAFANA_PORT}/api"

    # Set admin password
    curl -sf -X PUT "${GRAFANA_API}/user/password" \
        -u "${GRAFANA_AUTH}" \
        -H "Content-Type: application/json" \
        -d '{"oldPassword":"admin","newPassword":"syswatch","confirmNew":"syswatch"}' \
        &>/dev/null || warn "Grafana password may already be set"

    GRAFANA_AUTH="admin:syswatch"

    # Add TimescaleDB / PostgreSQL datasource
    curl -sf -X POST "${GRAFANA_API}/datasources" \
        -u "${GRAFANA_AUTH}" \
        -H "Content-Type: application/json" \
        -d "{
          \"name\": \"syswatch-timescaledb\",
          \"type\": \"postgres\",
          \"url\": \"localhost:5432\",
          \"database\": \"${PG_DB}\",
          \"user\": \"${PG_USER}\",
          \"secureJsonData\": {\"password\": \"${PG_PASSWORD}\"},
          \"jsonData\": {\"sslmode\": \"disable\", \"postgresVersion\": $(( ${PG_VERSION} * 100 )), \"timescaledb\": true}
        }" &>/dev/null || warn "Grafana datasource may already exist"

    log "Grafana datasource configured"
}

# ── Prometheus scrape config ───────────────────────────────────────────────────
_configure_prometheus() {
    log "Configuring Prometheus scrape targets"

    cat > /etc/prometheus/prometheus.yml <<EOF
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['localhost:${ALERTMANAGER_PORT}']

rule_files: []

scrape_configs:
  - job_name: 'syswatch-server'
    static_configs:
      - targets: ['localhost:9091']

  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:${PROMETHEUS_PORT}']
EOF

    chown prometheus:prometheus /etc/prometheus/prometheus.yml
    log "Prometheus config written: /etc/prometheus/prometheus.yml"
}

# ── CLI shim ──────────────────────────────────────────────────────────────────
_install_cli_shim() {
    log "Installing syswatch CLI shim"

    cat > /usr/local/bin/syswatch <<EOF
#!/usr/bin/env bash
# syswatch CLI — thin wrapper around the installed server venv entrypoint
exec sudo -u ${SYSWATCH_USER} \\
    SYSWATCH_CONFIG=${SERVER_CONFIG_DIR}/config.yaml \\
    SYSWATCH_HOME=${SERVER_INSTALL_DIR} \\
    SYSWATCH_WEB_URL=http://127.0.0.1:${SERVER_HTTP_PORT} \\
    ${SERVER_VENV}/bin/syswatch-server "\$@"
EOF
    chmod 755 /usr/local/bin/syswatch
    log "CLI available: syswatch --help"
}

# ══════════════════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════════════════
case "${MODE}" in
    agent)  install_agent  ;;
    server) install_server ;;
esac
