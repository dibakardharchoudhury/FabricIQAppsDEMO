#!/usr/bin/env python3
"""Cross-platform launcher for the "Initialize Your Fabric Demo" web app.

One file for Windows, macOS and Linux. It checks prerequisites, installs any
missing Python dependencies, signs you in to Azure if needed, then starts the
local web app (which opens your browser at http://127.0.0.1:5000).

Run it however is convenient:
    python launch.py        (Windows)
    python3 launch.py       (macOS / Linux)
or double-click one of the thin shims that just call this file:
    Start Fabric Demo.cmd   (Windows)
    start-fabric-demo.sh    (macOS / Linux)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
SERVER = HERE / "webapp" / "server.py"


def fail(message: str) -> None:
    print(f"\n  [X] {message}\n")
    try:
        input("  Press Enter to close...")
    except EOFError:
        pass
    raise SystemExit(1)


def main() -> int:
    print("\n  ===================================================")
    print("   Initialize Your Fabric Demo - local web app")
    print("  ===================================================\n")

    # 1. Install / update Python dependencies (quiet).
    print("  [1/3] Checking dependencies...")
    deps = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)]
    )
    if deps.returncode != 0:
        fail("Could not install dependencies. Check your internet connection and try again.")

    # 2. Azure CLI present?
    az = shutil.which("az")
    if not az:
        fail(
            "Azure CLI (az) is not installed - it is needed to sign in.\n"
            "      Install it from https://aka.ms/installazurecli then run this again."
        )

    # 3. Signed in? If not, sign in (opens a browser).
    print("  [2/3] Checking Azure sign-in...")
    signed_in = subprocess.run(
        [az, "account", "show"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False
    ).returncode == 0
    if not signed_in:
        print("        You are not signed in. A browser window will open for sign-in...")
        if subprocess.run([az, "login"], shell=False).returncode != 0:
            fail("Sign-in failed. Please run this again.")

    # 4. Start the app. server.py opens your browser automatically.
    print("  [3/3] Starting the app...\n")
    print("   Your browser will open at http://127.0.0.1:5000")
    print("   Keep this window open while you use the app. Press Ctrl+C to stop.\n")
    try:
        return subprocess.run([sys.executable, str(SERVER)]).returncode
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
