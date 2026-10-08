import os
from pathlib import Path

import uvicorn

from .service import create_app


def main() -> None:
    token = os.environ.get("HYDRO_LOCAL_API_TOKEN", "")
    root = Path(os.environ.get("HYDRO_LOCAL_STATE_DIR", ".runtime"))
    app = create_app(root, token)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("HYDRO_LOCAL_PORT", "8088")))


if __name__ == "__main__":
    main()
