"""Run the web application with ``python -m web``."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        workers=1,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
