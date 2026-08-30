from __future__ import annotations

import uvicorn

from app.api.app import create_app
from app.api.autonomous import attach_autonomous_routes
from app.api.composition import build_product_service
from app.api.operator import attach_operator_routes
from app.api.readiness import OperationalReadinessChecker, attach_readiness_route
from app.api.trace import attach_trace_routes
from app.core.runtime_identity import assert_runtime_fingerprint
from app.core.settings import get_settings
from app.persistence.schema import require_database_schema_current


def build_app():
    runtime_fingerprint = assert_runtime_fingerprint()
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
    application.state.runtime_fingerprint = runtime_fingerprint
    attach_autonomous_routes(application, service)
    attach_trace_routes(application, service)
    attach_operator_routes(application, service)
    attach_readiness_route(application, OperationalReadinessChecker(settings))
    return application


app = build_app()


def run() -> None:
    uvicorn.run(
        "app.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        # Uvicorn's default Windows loop factory selects ProactorEventLoop,
        # which psycopg's async implementation does not support. ``none``
        # preserves the Selector policy configured by app package startup.
        loop="none",
    )


if __name__ == "__main__":
    run()
