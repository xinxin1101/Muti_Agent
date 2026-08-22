from __future__ import annotations

import uvicorn

from app.api.app import create_app
from app.api.autonomous import attach_autonomous_routes
from app.api.composition import build_product_service
from app.api.operator import attach_operator_routes
from app.api.trace import attach_trace_routes
from app.core.settings import get_settings
from app.persistence.schema import require_database_schema_current


def build_app():
    settings = get_settings()
    if settings.database_url is None:
        raise ValueError("DEVFLOW_DATABASE_URL is required by the product API")

    service = build_product_service(settings)

    async def startup_check() -> None:
        await require_database_schema_current(
            settings.database_url,
            echo=settings.database_echo,
        )

    application = create_app(
        service,
        close_service=True,
        startup_check=startup_check,
    )
    attach_autonomous_routes(application, service)
    attach_trace_routes(application, service)
    attach_operator_routes(application, service)
    return application


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
