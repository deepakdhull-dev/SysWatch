from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

SERVICE_NAME = "syswatch-agent"
CONFIG_PATH = Path("/etc/syswatch/agent.yaml")
CERTS_DIR = Path("/etc/syswatch/certs")
BACKUP_DIR = Path("/etc/syswatch/.backup")
EXPECTED_ZIP_FILES = {"agent.yaml", "ca.crt", "client.crt", "client.key"}
ZIP_FILES_DESTINATIONS = {
    "agent.yaml": CONFIG_PATH,
    "ca.crt": CERTS_DIR / "ca.crt",
    "client.crt": CERTS_DIR / "client.crt",
    "client.key": CERTS_DIR / "client.key",
}


def require_root():
    if os.getuid() != 0:
        print(
            "Error: this command requires root privileges.\n "
            f"Run: sudo syswatch {' '.join(sys.argv[1:])}",
            file=sys.stderr,
        )
        sys.exit(1)


def systemctl(action, check=True):
    capture = action not in ("status",)
    return subprocess.run(
        ["systemctl", action, SERVICE_NAME],
        check=check,
        text=True,
        capture_output=capture,
    )


def cmd_start(args):
    require_root()
    try:
        systemctl("start")
        print(f"Started {SERVICE_NAME}")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"Failed to start {SERVICE_NAME}: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(
            "Error: systemctl not found. Is this a systemd-based system?",
            file=sys.stderr,
        )
        return 1


def cmd_stop(args):
    require_root()
    try:
        systemctl("stop")
        print(f"Stopped {SERVICE_NAME}.")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"Failed to stop {SERVICE_NAME}: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("Error: systemctl not found.", file=sys.stderr)
        return 1


def cmd_status(args):
    try:
        result = systemctl("status", check=False)
        return result.returncode
    except FileNotFoundError:
        print("Error: systemctl not found.", file=sys.stderr)
        return 1


def cmd_update(args):
    require_root()
    zip_path = Path(args.zip_file)
    if not zip_path.exists():
        print(f"Error: file not found: {zip_path}", file=sys.stderr)
        return 1

    if not zipfile.is_zipfile(zip_path):
        print(f"Error: not a valid zip file: {zip_path}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(zip_path) as zf:
        zip_names = {Path(name).name for name in zf.namelist()}
        missing = EXPECTED_ZIP_FILES - zip_names
        if missing:
            print(
                f"Error: zip is missing required files: {', '.join(sorted(missing))}",
                file=sys.stderr,
            )
            return 1
        unexpected = zip_names - EXPECTED_ZIP_FILES
        if unexpected:
            print(
                f"Error: zip contains unexpected files: {', '.join(sorted(unexpected))}\n"
                f"Accepted files: {', '.join(sorted(EXPECTED_ZIP_FILES))}",
                file=sys.stderr,
            )
            return 1

    print(f"Stopping {SERVICE_NAME} ...")
    try:
        systemctl("stop")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"Note: could not stop {SERVICE_NAME} (may already be stopped).")

    backup_current_files()
    print("Current config and certs are backed up")

    print(f"applying {zip_path}...")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for entry in zf.infolist():
                filename = Path(entry.filename).name
                if filename not in ZIP_FILES_DESTINATIONS:
                    continue
                dest = ZIP_FILES_DESTINATIONS[filename]
                dest.parent.mkdir(parents=True, exist_ok=True)

                with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
                    tmp.write(zf.read(entry.filename))
                    tmp_path = Path(tmp.name)
                tmp_path.replace(dest)
                print(f"Installed {dest}")
        key_dest = ZIP_FILES_DESTINATIONS["client.key"]
        key_dest.chmod(0o600)
        print(f"Set permission 600 on {key_dest}")
    except Exception as exc:
        print(f"Error during extraction: {exc}", file=sys.stderr)
        print("Restoring backup...", file=sys.stderr)
        restore_backup()
        print("Backup restored.")
        try:
            systemctl("start", check=False)
            print(f"Restarted {SERVICE_NAME} with previous config.")
        except FileNotFoundError:
            pass
        return 1

    print(f"Starting {SERVICE_NAME}")
    try:
        systemctl("start")
        print(f"Update complete. {SERVICE_NAME} is running with new config.")
        cleanup_backup()
        return 0
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(
            f"Error: {SERVICE_NAME} failed to start with new config: {exc}",
            file=sys.stderr,
        )
        print(
            "Restoring backup and restarting with previous config...", file=sys.stderr
        )
        restore_backup()
        try:
            systemctl("start", check=False)
        except FileNotFoundError:
            pass
        print("Backup restored.")
        return 1


def backup_current_files():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    candidates = [CONFIG_PATH] + list(CERTS_DIR.glob("*"))
    for src in candidates:
        if src.is_file():
            shutil.copy2(src, BACKUP_DIR / src.name)


def restore_backup():
    if not BACKUP_DIR.exists():
        return
    for src in BACKUP_DIR.iterdir():
        if not src.is_file():
            continue
        if src.name == "agent.yaml":
            dest = CONFIG_PATH
        elif src.suffix in (".crt", ".key"):
            dest = CERTS_DIR / src.name
        else:
            continue
        shutil.copy2(src, dest)


def cleanup_backup():
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)


def load_yaml():
    if not CONFIG_PATH.exists():
        print(f"Error: config not found at {CONFIG_PATH}.", file=sys.stderr)
        print("Has the agent been installed? Run install.sh first.", file=sys.stderr)
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        try:
            data = yaml.safe_load(f)
            return data or {}
        except yaml.YAMLError as exc:
            print(f"Error: malformed config at {CONFIG_PATH}: {exc}", file=sys.stderr)
            sys.exit(1)


def save_yaml(data):
    with tempfile.NamedTemporaryFile(
        mode="w", dir=CONFIG_PATH.parent, delete=False, suffix=".tmp"
    ) as tmp:
        yaml.dump(data, tmp, default_flow_style=False, sort_keys=False)
        tmp_path = Path(tmp.name)
        tmp_path.replace(CONFIG_PATH)


def cmd_service_add(args):
    require_root()
    data = load_yaml()
    services = data.setdefault("agent", {}).setdefault("services", [])
    if args.name in services:
        print(f"'{args.name}' is already in the monitored services list.")
        return 0
    services.append(args.name)
    save_yaml(data)
    print(f"Added '{args.name}' to monitored services.")
    return restart_if_running()


def cmd_service_delete(args):
    require_root()
    data = load_yaml()
    services = data.get("agent", {}).get("services", [])

    if args.name not in services:
        print(
            f"Error: '{args.name}' is not in the monitored services list.",
            file=sys.stderr,
        )
        if services:
            print(f"Current list: {', '.join(services)}", file=sys.stderr)
        else:
            print("Current list is empty.", file=sys.stderr)
        return 1

    services.remove(args.name)
    data["agent"]["services"] = services
    save_yaml(data)
    print(f"Removed '{args.name}' from monitored services.")

    return restart_if_running()


def cmd_service_list(args):
    data = load_yaml()
    services: list = data.get("agent", {}).get("services", [])

    if not services:
        print("No services currently monitored.")
        print("Add one with: syswatch service add <name>")
        return 0

    print("Monitored services:")
    for svc in sorted(services):
        print(f"  {svc}")
    return 0


def restart_if_running():
    try:
        result = systemctl("is-active", check=False)
        if result.stdout.strip() == "active":
            systemctl("restart")
            print(f"Restarted {SERVICE_NAME} to apply changes.")
        else:
            print(
                f"Note: {SERVICE_NAME} is not running. "
                "Changes take effect on next start."
            )
        return 0
    except FileNotFoundError:
        print("Warning: systemctl not found, skipping restart.")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"Warning: failed to restart {SERVICE_NAME}: {exc}", file=sys.stderr)
        return 1


def build_parser():
    parser = argparse.ArgumentParser(
        prog="syswatch_agent", description="syswatch-agent management CLI"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_start = sub.add_parser("start", help="Start the syswatch-agent service")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop the syswatch-agent service")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="Show the syswatch-agent service status")
    p_status.set_defaults(func=cmd_status)

    p_update = sub.add_parser(
        "update",
        help="Apply a server-generated zip bundle (new certs + config)",
    )
    p_update.add_argument(
        "zip_file",
        metavar="<file.zip>",
        help="Path to the zip bundle downloaded from the syswatch server",
    )
    p_update.set_defaults(func=cmd_update)

    p_service = sub.add_parser(
        "service",
        help="Manage monitored systemd services",
    )
    service_sub = p_service.add_subparsers(
        dest="service_command",
        metavar="<subcommand>",
    )

    p_svc_add = service_sub.add_parser("add", help="Add a service to monitor")
    p_svc_add.add_argument(
        "name",
        metavar="<service-name>",
        help="Systemd service name (e.g. nginx, postgresql)",
    )
    p_svc_add.set_defaults(func=cmd_service_add)

    p_svc_del = service_sub.add_parser(
        "delete", help="Remove a service from monitoring"
    )
    p_svc_del.add_argument(
        "name",
        metavar="<service-name>",
        help="Systemd service name to remove",
    )
    p_svc_del.set_defaults(func=cmd_service_delete)

    p_svc_list = service_sub.add_parser("list", help="List monitored services")
    p_svc_list.set_defaults(func=cmd_service_list)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        if args.command == "service":
            parser.parse_args(["service", "--help"])
        else:
            parser.print_help()
        sys.exit(1)
    exit_code = args.func(args)
    sys.exit(exit_code if exit_code is not None else 0)
