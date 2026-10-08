import os
import argparse
import asyncio
from pathlib import Path

import uvicorn

from .service import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Hydro durability validation and read-only source diagnostics.")
    probes = parser.add_mutually_exclusive_group()
    probes.add_argument("--probe-live-sources", metavar="EQUIPMENT_ID")
    probes.add_argument("--probe-live-rca", metavar="EQUIPMENT_ID")
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
    token = os.environ.get("HYDRO_LOCAL_API_TOKEN", "")
    root = Path(os.environ.get("HYDRO_LOCAL_STATE_DIR", ".runtime"))
    app = create_app(root, token)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("HYDRO_LOCAL_PORT", "8088")))


if __name__ == "__main__":
    main()
