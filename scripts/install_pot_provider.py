"""Install the pinned local PO Token provider for Render builds."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import deno


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_ROOT = ROOT / ".local-build" / "bgutil-ytdlp-pot-provider"
PROVIDER_COMMIT = "26475e9d3665b972c4aeb83b4dbaafcf64b88438"


def main() -> None:
    PROVIDER_ROOT.parent.mkdir(parents=True, exist_ok=True)
    if not PROVIDER_ROOT.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "2.0.2", "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git", str(PROVIDER_ROOT)],
            check=True,
        )
    current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROVIDER_ROOT, text=True).strip()
    if current != PROVIDER_COMMIT:
        raise RuntimeError("Unexpected PO Token provider revision")
    server = PROVIDER_ROOT / "server"
    deno_dir = ROOT / ".local-build" / "deno-cache"
    deno_dir.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "DENO_DIR": str(deno_dir)}
    subprocess.run(
        [deno.find_deno_bin(), "install", "--allow-scripts=npm:canvas", "--frozen"],
        cwd=server,
        env=environment,
        check=True,
    )
    (server / ".assistant-ready").write_text(PROVIDER_COMMIT, encoding="ascii")
    print(f"PO Token provider {PROVIDER_COMMIT[:7]} installed")


if __name__ == "__main__":
    main()
