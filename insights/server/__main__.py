"""``python -m insights.server``: run the server with host/port from config.

uvicorn is imported lazily here and only here; it is not a package dependency.
"""

from __future__ import annotations

from insights.config import load_config


def main() -> None:
    import uvicorn

    config = load_config()
    uvicorn.run(
        "insights.server.app:create_app",
        factory=True,
        host=config.server.host,
        port=config.server.port,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
