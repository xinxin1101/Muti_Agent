from __future__ import annotations

from app.api.catalog import PostgresProductCatalog
from app.api.service import ProductRuntimeService
from app.core.settings import Settings
from app.dispatch import DramatiqTaskDispatcher
from app.persistence import PostgresEvidenceStore
from app.workers.executor import ManagedProjectWorkspaceResolver
from app.workspace import ManagedProjectProvisioner


def build_product_service(settings: Settings) -> ProductRuntimeService:
    if settings.database_url is None:
        raise ValueError("DEVFLOW_DATABASE_URL is required by the product API")

    from app.workers.tasks import execute_devflow_task

    evidence_store = PostgresEvidenceStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    catalog = PostgresProductCatalog.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    repository_root = settings.workspace_root / "repos"
    provisioner = ManagedProjectProvisioner(repository_root)
    resolver = ManagedProjectWorkspaceResolver(repository_root)
    dispatcher = DramatiqTaskDispatcher(store=evidence_store, actor=execute_devflow_task)
    return ProductRuntimeService(
        catalog=catalog,
        evidence_store=evidence_store,
        provisioner=provisioner,
        workspace_resolver=resolver,
        dispatcher=dispatcher,
    )
