"""Cross-process guards for deployment resources shared by workspace workers."""

from __future__ import annotations

import hashlib
import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID


def lock_directory() -> Path:
    return Path.home() / ".fabric-demo" / "deployment-locks"


@contextmanager
def resource_lock(tenant: str, resource: str, *, timeout: float = 3 * 3600):
    directory = lock_directory()
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{tenant.casefold()}:{resource.casefold()}".encode()).hexdigest()
    with (directory / f"{key}.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        waiting = False
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for shared deployment resource: {resource}.")
                if not waiting:
                    print(f"Waiting for shared deployment resource: {resource}.", flush=True)
                    waiting = True
                time.sleep(0.25)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def foundry_resource_suffix(workspace: str) -> str:
    """Keep workspace-bound resources distinct even inside one shared project."""
    return "-" + UUID(workspace).hex
