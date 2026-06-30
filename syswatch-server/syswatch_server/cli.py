from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request

SERVICE_NAME = "syswatch-server"
DEFAULT_WEB_URL = "http://127.0.0.1:8080"


def _run(cmd: list[str], cwd: str | None = None) -> int:
    try:
        return subprocess.call(cmd, cwd=cwd)
    except FileNotFoundError:
        print(f"Command not found: {cmd[0]}", file=sys.stderr)
        return 127


def cmd_start(_args: argparse.Namespace) -> int:
    return _run(["systemctl", "start", SERVICE_NAME])


def cmd_stop(_args: argparse.Namespace) -> int:
    return _run(["systemctl", "stop", SERVICE_NAME])


def cmd_restart(_args: argparse.Namespace) -> int:
    return _run(["systemctl", "restart", SERVICE_NAME])


def cmd_status(_args: argparse.Namespace) -> int:
    return _run(["systemctl", "status", SERVICE_NAME, "--no-pager"])


def cmd_logs(args: argparse.Namespace) -> int:
    cmd = ["journalctl", "-u", SERVICE_NAME]
    if args.follow:
        cmd.append("-f")
    else:
        cmd.extend(["-n", str(args.lines)])
    return _run(cmd)


def cmd_migrate(_args: argparse.Namespace) -> int:
    workdir = os.environ.get("SYSWATCH_HOME", "/opt/syswatch-server")
    if not os.path.isdir(workdir):
        print(f"Install directory not found: {workdir}", file=sys.stderr)
        print(
            "Set SYSWATCH_HOME to the directory containing alembic.ini.",
            file=sys.stderr,
        )
        return 1

    alembic_ini = os.path.join(workdir, "alembic.ini")
    if not os.path.isfile(alembic_ini):
        print(f"alembic.ini not found at {alembic_ini}", file=sys.stderr)
        return 1

    # Use the venv's own alembic binary (same venv this CLI is running from),
    # not a bare "alembic" lookup on $PATH — the venv's bin/ is not on PATH
    # when invoked via the syswatch-server console_script entry point.
    venv_bin = os.path.dirname(sys.executable)
    alembic_bin = os.path.join(venv_bin, "alembic")
    if not os.path.isfile(alembic_bin):
        alembic_bin = "alembic"  # fall back to PATH lookup as a last resort

    return _run([alembic_bin, "-c", alembic_ini, "upgrade", "head"], cwd=workdir)


def cmd_health(_args: argparse.Namespace) -> int:
    url = (
        os.environ.get("SYSWATCH_WEB_URL", DEFAULT_WEB_URL).rstrip("/") + "/api/health"
    )
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode()
            print(f"OK ({resp.status}): {body}")
            return 0
    except urllib.error.URLError as exc:
        print(f"UNHEALTHY: cannot reach {url}: {exc}", file=sys.stderr)
        return 1


def cmd_open(_args: argparse.Namespace) -> int:
    url = os.environ.get("SYSWATCH_WEB_URL", DEFAULT_WEB_URL)
    return _run(["xdg-open", url])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="syswatch-server",
        description="Manage the syswatch-server systemd service.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="Start the service.").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="Stop the service.").set_defaults(func=cmd_stop)
    sub.add_parser("restart", help="Restart the service.").set_defaults(
        func=cmd_restart
    )
    sub.add_parser("status", help="Show service status.").set_defaults(func=cmd_status)

    logs_p = sub.add_parser("logs", help="Show service logs.")
    logs_p.add_argument(
        "-f", "--follow", action="store_true", help="Follow log output."
    )
    logs_p.add_argument(
        "-n", "--lines", type=int, default=50, help="Lines to show (default 50)."
    )
    logs_p.set_defaults(func=cmd_logs)

    sub.add_parser(
        "migrate", help="Apply DB migrations (alembic upgrade head)."
    ).set_defaults(func=cmd_migrate)
    sub.add_parser("health", help="Check server health endpoint.").set_defaults(
        func=cmd_health
    )
    sub.add_parser("open", help="Open the dashboard in a browser.").set_defaults(
        func=cmd_open
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
