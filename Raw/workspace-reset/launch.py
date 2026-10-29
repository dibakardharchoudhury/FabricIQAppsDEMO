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

import difflib
import json
import os
import re
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
# Windows can take about two seconds to report a closed localhost port.
PORT_PROBE_TIMEOUT = 3


def only_generated_redirect_additions(checkout: Path, status: str) -> bool:
    relative = "HydroOperationsApp/rayfin/rayfin.yml"
    if status.rstrip("\r\n") != f" M {relative}":
        return False
    committed = subprocess.run(
        ["git", "-C", str(checkout), "show", f"HEAD:{relative}"],
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
    ).stdout.splitlines()
    current = (checkout / "HydroOperationsApp" / "rayfin" / "rayfin.yml").read_text(encoding="utf-8").splitlines()
    keys = [index for index, line in enumerate(committed) if line.strip() == "allowedRedirectUris:"]
    if len(keys) != 1:
        return False
    start = keys[0]
    indent = len(committed[start]) - len(committed[start].lstrip())
    end = start + 1
    while end < len(committed):
        line = committed[end]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        end += 1
    origin = re.compile(r" {" + str(indent + 2) + r"}- https://[a-z0-9][a-z0-9-]*\.webapp\.fabricapps\.net")
    added = False
    for tag, old_start, _old_end, new_start, new_end in difflib.SequenceMatcher(
        a=committed, b=current, autojunk=False,
    ).get_opcodes():
        if tag == "equal":
            continue
        if (tag != "insert" or not start < old_start <= end
                or not all(origin.fullmatch(line) for line in current[new_start:new_end])):
            return False
        added = True
    return added


def server_path() -> Path:
    config = HERE / "launcher.local.json"
    if not config.exists():
        return SERVER
    settings = json.loads(config.read_text(encoding="utf-8"))
    if not isinstance(settings, dict) or not isinstance(settings.get("checkout"), str) or not settings["checkout"].strip():
        raise ValueError("launcher.local.json requires a checkout path.")
    checkout = Path(settings["checkout"]).expanduser().resolve(strict=True)
    server = checkout / "Raw" / "workspace-reset" / "webapp" / "server.py"
    if not server.is_file():
        raise ValueError(f"No Fabric Demo server exists in {checkout}.")
    status = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    if status.stdout.strip():
        if not only_generated_redirect_additions(checkout, status.stdout):
            raise ValueError(
                f"The configured deployment checkout has modified tracked files: {checkout}.\n"
                f"{status.stdout.rstrip()}"
            )
        print("  Retaining generated Rayfin hosting redirects; tracked source files are unchanged.")
    return server


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
        with urllib.request.urlopen(APP_URL, timeout=PORT_PROBE_TIMEOUT) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        fail(f"Could not identify the service on port 5000 (HTTP {exc.code}).")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if isinstance(reason, ConnectionRefusedError):
            return
        fail(f"Could not identify the service on port 5000: {reason}. Retry after it becomes responsive.")

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
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if not isinstance(reason, (ConnectionError, TimeoutError)):
            fail(f"Could not stop the existing local app: {reason}")
        print(f"  Connection closed while stopping the existing app; verifying its port is released ({reason}).")

    for attempt in range(30):
        try:
            urllib.request.urlopen(APP_URL, timeout=PORT_PROBE_TIMEOUT).close()
        except urllib.error.HTTPError as exc:
            fail(f"Could not verify the existing local app stopped (HTTP {exc.code}).")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if isinstance(reason, ConnectionRefusedError):
                return
            if not isinstance(reason, (ConnectionError, TimeoutError)):
                fail(f"Could not verify the existing local app stopped: {reason}")
            if attempt == 0:
                print(f"  Existing app is closing; waiting for port 5000 to be released ({reason}).")
        time.sleep(0.2)
    fail("The existing local app did not stop. Use its Stop server button, then retry.")


def main() -> int:
    print("\n  ===================================================")
    print("   Initialize Your Fabric Demo - local web app")
    print("  ===================================================\n")

    try:
        selected_server = server_path()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        fail(f"Could not verify the deployment checkout: {exc}")
    print(f"  Deployment source: {selected_server.parent.parent.parent.parent}")

    # 1. Install / update Python dependencies (quiet).
    print("  [1/2] Checking dependencies...")
    deps = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(selected_server.parent.parent / "requirements.txt")]
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
    env.setdefault("FABRIC_UI_PIPELINE_DEFAULTS_FILE", str(HERE / "webapp" / "pipeline-defaults.local.json"))
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
    proc = subprocess.Popen([sys.executable, str(selected_server)], env=env, **popen_kwargs)

    last_error = ""
    for attempt in range(60):
        try:
            urllib.request.urlopen(APP_URL, timeout=1).close()
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_error = str(exc)
            if proc.poll() is not None:
                fail("The server stopped before it was ready (is port 5000 already in use?).")
            if attempt == 0:
                print(f"  Waiting for the local app to become ready ({exc}).")
            time.sleep(0.5)
    else:
        fail(f"The server did not become ready in time. Last connection error: {last_error}. "
             f"Try opening {APP_URL} manually.")

    webbrowser.open(APP_URL)
    print(f"   The app is running at {APP_URL} (PID {proc.pid}).")
    print("   Sign in with Microsoft on the page, then use the tools.")
    print("   You can close this window -- the app keeps running.")
    print("   To stop it, use the 'Stop server' button on the page.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
