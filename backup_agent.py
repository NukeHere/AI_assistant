from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_API_URL = "https://ai-assistant-4yn0.onrender.com/v1/message"
DEFAULT_CLIENT_ID = "primary-user"
DEFAULT_KEEP = 10
DEFAULT_INTERVAL_SECONDS = 10


def load_local_env() -> None:
    env_path = Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "_.-" else "_" for char in value)
    return cleaned.strip("._") or "default"


def snapshot_url(api_url: str) -> str:
    stripped = api_url.rstrip("/")
    for suffix in ["/v1/message", "/v1/history", "/v1/chat/simple"]:
        if stripped.endswith(suffix):
            return stripped[: -len(suffix)] + "/v1/snapshot"
    if stripped.endswith("/v1/snapshot"):
        return stripped
    return stripped + "/v1/snapshot"


def request_snapshot(url: str, token: str, payload_body: dict[str, object], timeout: int = 60) -> dict[str, object]:
    payload = json.dumps(payload_body, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Snapshot response must be a JSON object")
    return data


def backup_filename(client_id: str, now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    return f"assistant-snapshot-{safe_name(client_id)}-{stamp}.json"


def snapshot_weight(snapshot: dict[str, object]) -> int:
    return sum(
        len(snapshot.get(key, []))
        for key in ["conversations", "messages", "memories", "memory_cells", "timed_memories", "telegram_users"]
        if isinstance(snapshot.get(key, []), list)
    )


def snapshot_section_count(snapshot: dict[str, object], section: str) -> int:
    value = snapshot.get(section, [])
    return len(value) if isinstance(value, list) else 0


def snapshot_needs_restore(remote_snapshot: dict[str, object], local_snapshot: dict[str, object]) -> bool:
    if snapshot_weight(remote_snapshot) < snapshot_weight(local_snapshot):
        return True
    critical_sections = ["telegram_users", "memory_cells", "timed_memories"]
    return any(
        snapshot_section_count(local_snapshot, section) > 0
        and snapshot_section_count(remote_snapshot, section) == 0
        for section in critical_sections
    )


def load_backup_snapshot(path: Path) -> dict[str, object]:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict):
        raise ValueError("Backup file must contain snapshot object")
    return snapshot


def desktop_snapshot_path(backup_dir: Path, client_id: str) -> Path:
    base_dir = backup_dir.parent if backup_dir.name == "backups" else backup_dir
    return base_dir / f"snapshot-{safe_name(client_id)}.json"


def latest_backup(backup_dir: Path, client_id: str) -> Path | None:
    candidates = list_backups(backup_dir, client_id)
    desktop_snapshot = desktop_snapshot_path(backup_dir, client_id)
    if desktop_snapshot.exists():
        candidates.append(desktop_snapshot)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def save_backup(backup_dir: Path, client_id: str, snapshot: dict[str, object]) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / backup_filename(client_id)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def list_backups(backup_dir: Path, client_id: str) -> list[Path]:
    pattern = f"assistant-snapshot-{safe_name(client_id)}-*.json"
    return sorted(backup_dir.glob(pattern), key=lambda path: path.name, reverse=True)


def prune_backups(backup_dir: Path, client_id: str, keep: int) -> list[Path]:
    removed: list[Path] = []
    for path in list_backups(backup_dir, client_id)[max(keep, 0) :]:
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def export_backup(url: str, token: str, client_id: str, backup_dir: Path, keep: int) -> Path:
    data = request_snapshot(url, token, {"action": "export", "client_id": client_id})
    snapshot = data.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("Export response does not contain snapshot object")
    path = save_backup(backup_dir, client_id, snapshot)
    prune_backups(backup_dir, client_id, keep)
    return path


def sync_backup(url: str, token: str, client_id: str, backup_dir: Path, keep: int) -> tuple[str, Path | None]:
    data = request_snapshot(url, token, {"action": "export", "client_id": client_id})
    snapshot = data.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("Export response does not contain snapshot object")

    latest = latest_backup(backup_dir, client_id)
    if latest is not None:
        local_snapshot = load_backup_snapshot(latest)
        if snapshot_needs_restore(snapshot, local_snapshot):
            restore_backup(url, token, client_id, latest)
            return "restored", latest

    path = save_backup(backup_dir, client_id, snapshot)
    prune_backups(backup_dir, client_id, keep)
    return "saved", path


def restore_backup(url: str, token: str, client_id: str, backup_path: Path) -> dict[str, object]:
    snapshot = load_backup_snapshot(backup_path)
    return request_snapshot(url, token, {"action": "import", "client_id": client_id, "snapshot": snapshot})


def config_from_env() -> tuple[str, str, str, Path, int]:
    load_local_env()
    api_url = os.getenv("ASSISTANT_API_URL", DEFAULT_API_URL)
    token = os.getenv("APP_API_TOKEN", "")
    client_id = os.getenv("ASSISTANT_CLIENT_ID", DEFAULT_CLIENT_ID)
    backup_dir = Path(os.getenv("ASSISTANT_BACKUP_DIR", Path.home() / ".ai_assistant" / "backups"))
    keep = int(os.getenv("ASSISTANT_BACKUP_KEEP", str(DEFAULT_KEEP)))
    if not token:
        raise ValueError("APP_API_TOKEN is required in .env or environment")
    return snapshot_url(api_url), token, client_id, backup_dir, keep


def run_once() -> tuple[str, Path | None]:
    url, token, client_id, backup_dir, keep = config_from_env()
    return sync_backup(url, token, client_id, backup_dir, keep)


def run_loop(interval_seconds: int) -> None:
    while True:
        try:
            action, path = run_once()
            if action == "restored":
                print(f"Backup restored to Render from: {path}", flush=True)
            else:
                print(f"Backup saved: {path}", flush=True)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            print(f"Backup failed: {error}", flush=True)
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup and restore AI Assistant Render memory snapshots.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("once", help="Create one backup now")
    loop_parser = subparsers.add_parser("loop", help="Create backups forever on an interval")
    loop_parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    restore_parser = subparsers.add_parser("restore", help="Restore latest or selected backup to Render")
    restore_parser.add_argument("path", nargs="?", default="latest")
    subparsers.add_parser("list", help="List local backups")
    args = parser.parse_args()

    url, token, client_id, backup_dir, keep = config_from_env()
    command = args.command or "once"
    if command == "once":
        action, path = sync_backup(url, token, client_id, backup_dir, keep)
        if action == "restored":
            print(f"Backup restored to Render from: {path}")
        else:
            print(f"Backup saved: {path}")
    elif command == "loop":
        run_loop(max(1, int(args.interval)))
    elif command == "restore":
        if args.path == "latest":
            backup_path = latest_backup(backup_dir, client_id)
            if backup_path is None:
                raise SystemExit("No backups found")
        else:
            backup_path = Path(args.path)
        result = restore_backup(url, token, client_id, backup_path)
        print(json.dumps({"restored_from": str(backup_path), "result": result}, ensure_ascii=False, indent=2))
    elif command == "list":
        paths = list_backups(backup_dir, client_id)
        desktop_snapshot = desktop_snapshot_path(backup_dir, client_id)
        if desktop_snapshot.exists():
            paths.append(desktop_snapshot)
        for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True):
            print(path)
    else:
        parser.error(f"Unknown command: {command}")


if __name__ == "__main__":
    main()


