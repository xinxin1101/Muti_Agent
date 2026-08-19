from __future__ import annotations

import uvicorn

from app.api.app import create_app
from app.api.composition import build_product_service
from app.core.settings import get_settings


def build_app():
    settings = get_settings()
    return create_app(build_product_service(settings), close_service=True)


app = build_app()


def run() -> None:
    uvicorn.run(
        "app.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    run()
