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

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
SERVER = HERE / "webapp" / "server.py"
APP_URL = "http://127.0.0.1:5000/"


def fail(message: str) -> None:
    print(f"\n  [X] {message}\n")
    try:
        input("  Press Enter to close...")
    except EOFError:
        pass
    raise SystemExit(1)


def stop_existing_server() -> None:
    """Stop an existing local Fabric Demo server so current code owns the port."""
    try:
        with urllib.request.urlopen(APP_URL, timeout=1) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError:
        return

    if "Initialize Your Fabric Demo" not in body:
        fail("Port 5000 is already used by another application. Stop it, then retry.")

    request = urllib.request.Request(
        APP_URL + "api/shutdown",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            fail("The existing local app is running a job. Wait for it to finish, then relaunch.")
        fail(f"Could not stop the existing local app (HTTP {exc.code}).")
    except urllib.error.URLError as exc:
        fail(f"Could not stop the existing local app: {exc.reason}")

    for _ in range(30):
        try:
            urllib.request.urlopen(APP_URL, timeout=0.5).close()
        except urllib.error.URLError:
            return
        time.sleep(0.2)
    fail("The existing local app did not stop. Use its Stop server button, then retry.")


def main() -> int:
    print("\n  ===================================================")
    print("   Initialize Your Fabric Demo - local web app")
    print("  ===================================================\n")

    # 1. Install / update Python dependencies (quiet).
    print("  [1/2] Checking dependencies...")
    deps = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)]
    )
    if deps.returncode != 0:
        fail("Could not install dependencies. Check your internet connection and try again.")

    # 2. Azure CLI present? (needed by the in-app sign-in.) We no longer force
    #    `az login` here -- signing in is the first step inside the app, where you
    #    pick your account in the browser and your tenant is detected for you.
    az = shutil.which("az")
    if not az:
        fail(
            "Azure CLI (az) is not installed - it is needed to sign in.\n"
            "      Install it from https://aka.ms/installazurecli then run this again."
        )

    # 3. Start the app as a background server that is NOT tied to this window,
    #    so you can close this window and keep using the page. The page's own
    #    Restart / Stop buttons manage the server's lifecycle from here on.
    print("  [2/2] Starting the app...\n")
    stop_existing_server()
    env = os.environ.copy()
    env["FABRIC_UI_NO_BROWSER"] = "1"  # this launcher opens the browser once, below
    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: outlives this console window.
        popen_kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, str(SERVER)], env=env, **popen_kwargs)

    for _ in range(60):
        try:
            urllib.request.urlopen(APP_URL, timeout=1).close()
            break
        except urllib.error.URLError:
            if proc.poll() is not None:
                fail("The server stopped before it was ready (is port 5000 already in use?).")
            time.sleep(0.5)
    else:
        fail("The server did not become ready in time. Try opening " + APP_URL + " manually.")

    webbrowser.open(APP_URL)
    print(f"   The app is running at {APP_URL} (PID {proc.pid}).")
    print("   Sign in with Microsoft on the page, then use the tools.")
    print("   You can close this window -- the app keeps running.")
    print("   To stop it, use the 'Stop server' button on the page.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
