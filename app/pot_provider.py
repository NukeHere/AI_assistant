"""Start the locally installed PO Token provider on loopback only."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / ".local-build" / "bgutil-ytdlp-pot-provider" / "server"
PROVIDER_COMMIT = "26475e9d3665b972c4aeb83b4dbaafcf64b88438"
PING_URL = "http://127.0.0.1:4416/ping"
_lock = Lock()
_process: subprocess.Popen[bytes] | None = None
_install_event = Event()
_install_thread: Thread | None = None


def _installed() -> bool:
    marker = SERVER / ".assistant-ready"
    return marker.is_file() and marker.read_text(encoding="ascii").strip() == PROVIDER_COMMIT


def _install() -> None:
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "install_pot_provider.py")],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=300,
            check=True,
        )
        print("PO Token provider installed", flush=True)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"PO Token provider installation failed: {type(error).__name__}", flush=True)
    finally:
        _install_event.set()


def prepare_async() -> None:
    """Prepare the provider after startup without delaying the webhook."""
    global _install_thread
    if os.getenv("YT_DLP_POT_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
        return
    if _installed():
        _install_event.set()
        return
    with _lock:
        if _install_thread is None:
            _install_thread = Thread(target=_install, name="pot-provider-install", daemon=True)
            _install_thread.start()


def _ready() -> bool:
    try:
        with urlopen(PING_URL, timeout=1) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def ensure_started() -> bool:
    """Return true only when the private provider answers its health check."""
    global _process
    if os.getenv("YT_DLP_POT_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
        return False
    if _ready():
        return True
    if not _installed():
        prepare_async()
        _install_event.wait(timeout=90)
    if not _installed():
        return False
    with _lock:
        if _ready():
            return True
        if _process is None or _process.poll() is not None:
            import deno

            environment = {**os.environ, "DENO_DIR": str(ROOT / ".local-build" / "deno-cache")}
            _process = subprocess.Popen(
                [deno.find_deno_bin(), "run", "--allow-env", "--allow-net", "--allow-ffi=.", "--allow-read=.", "../src/main.ts", "--host", "127.0.0.1"],
                cwd=SERVER / "node_modules",
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and _process.poll() is None:
            if _ready():
                return True
            time.sleep(0.25)
    return _ready()


def stop_if_owned() -> None:
    """Release Deno memory after a download attempt on small hosts."""
    global _process
    with _lock:
        process = _process
        _process = None
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
