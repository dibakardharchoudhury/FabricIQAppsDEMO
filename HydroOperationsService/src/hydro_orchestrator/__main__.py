import os
import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from .live_sources import SourceFailure
from .service import RuntimeConfiguration, create_app, create_runtime_app


def prepare_runtime_user(state: Path) -> None:
    user = os.environ.get("HYDRO_RUNTIME_USER", "")
    if not user:
        return
    if sys.platform != "linux":
        raise PermissionError("Container runtime user initialization requires Linux.")
    import pwd

    try:
        account = pwd.getpwnam(user)
    except KeyError:
        raise PermissionError("Container runtime account is unavailable.") from None
    if account.pw_uid <= 0 or account.pw_gid <= 0:
        raise PermissionError("The application runtime must use a nonroot account.")
    if os.geteuid() != 0:
        if os.geteuid() != account.pw_uid or os.getegid() != account.pw_gid:
            raise PermissionError("The application runtime account does not match.")
        return

    if state == Path("/home/session/hydro"):
        directories = (Path("/home/session"), state)
    elif state == Path("/var/lib/hydro"):
        directories = (state,)
    else:
        raise PermissionError("Privileged initialization is limited to dedicated container state.")
    if any(directory.is_symlink() for directory in directories):
        raise PermissionError("Container state directories cannot be symbolic links.")
    for directory in directories:
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chown(directory, account.pw_uid, account.pw_gid, follow_symlinks=False)
        directory.chmod(0o700)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    if os.geteuid() != account.pw_uid or os.getegid() != account.pw_gid:
        raise PermissionError("Application runtime privileges were not dropped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Hydro durability validation and read-only source diagnostics.")
    probes = parser.add_mutually_exclusive_group()
    probes.add_argument("--probe-live-sources", metavar="EQUIPMENT_ID")
    probes.add_argument("--probe-live-rca", metavar="EQUIPMENT_ID")
    probes.add_argument("--serve-delegated", action="store_true")
    args = parser.parse_args()
    if args.probe_live_sources:
        from .live_sources import probe_live_sources

        raise SystemExit(0 if asyncio.run(probe_live_sources(args.probe_live_sources)) else 1)
    if args.probe_live_rca:
        from .foundry_rca import probe_live_rca

        endpoint = os.environ.get("HYDRO_FOUNDRY_PROJECT_ENDPOINT", "")
        if not endpoint:
            parser.error("Set HYDRO_FOUNDRY_PROJECT_ENDPOINT for the read-only RCA probe.")
        asyncio.run(probe_live_rca(args.probe_live_rca, endpoint))
        return
    if args.serve_delegated:
        configured = os.environ.get("HYDRO_ORCHESTRATOR_CONFIG", "")
        if not configured or len(configured) > 100000 or not os.environ.get("HYDRO_FABRIC_SOURCE_CONFIG"):
            parser.error("Delegated runtime requires explicit bounded HYDRO_ORCHESTRATOR_CONFIG "
                         "and HYDRO_FABRIC_SOURCE_CONFIG; local deployment fallback is not enabled.")
        root_value = os.environ.get("HYDRO_RUNTIME_STATE_DIR", "")
        if not root_value or not Path(root_value).is_absolute():
            parser.error("Set HYDRO_RUNTIME_STATE_DIR to an explicit absolute state directory.")
        try:
            configuration = RuntimeConfiguration.model_validate_json(configured)
            prepare_runtime_user(Path(root_value))
            app = asyncio.run(create_runtime_app(Path(root_value), configuration))
        except ValidationError:
            parser.error("Delegated runtime configuration is invalid; values suppressed.")
        except SourceFailure:
            parser.error("Packaged source bridge configuration verification failed; values suppressed.")
        except OSError as error:
            parser.error(f"Runtime state directory is unavailable ({type(error).__name__}); values suppressed.")
        uvicorn.run(app, host=os.environ.get("HYDRO_RUNTIME_HOST", "127.0.0.1"),
                    port=int(os.environ.get("HYDRO_RUNTIME_PORT", "8088")), access_log=False)
        return
    token = os.environ.get("HYDRO_LOCAL_API_TOKEN", "")
    root = Path(os.environ.get("HYDRO_LOCAL_STATE_DIR", ".runtime"))
    app = create_app(root, token)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("HYDRO_LOCAL_PORT", "8088")))


if __name__ == "__main__":
    main()
