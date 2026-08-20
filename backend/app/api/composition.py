from __future__ import annotations

from app.api.catalog import PostgresProductCatalog
from app.api.github_publication import ProductRuntimeServiceWithGitHubPublication
from app.core.settings import Settings
from app.dispatch import DurableDramatiqTaskDispatcher
from app.persistence import (
    PostgresDAGStore,
    PostgresDispatchAttemptStore,
    PostgresEvidenceStore,
    PostgresGitHubPublicationStore,
)
from app.publication import GitHubPublicationGateway
from app.workers.executor import ManagedProjectWorkspaceResolver
from app.workspace import ManagedProjectProvisioner


def build_product_service(settings: Settings) -> ProductRuntimeServiceWithGitHubPublication:
    if settings.database_url is None:
        raise ValueError("DEVFLOW_DATABASE_URL is required by the product API")

    from app.workers.tasks import execute_devflow_task

    evidence_store = PostgresEvidenceStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    dag_store = PostgresDAGStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    dispatch_store = PostgresDispatchAttemptStore.from_url(
        settings.database_url,
        echo=settings.database_echo,
    )
    publication_store = PostgresGitHubPublicationStore.from_url(
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
    dispatcher = DurableDramatiqTaskDispatcher(
        run_store=evidence_store,
        ledger=dispatch_store,
        actor=execute_devflow_task,
    )
    github_publisher = (
        None
        if settings.github_token is None
        else GitHubPublicationGateway(
            settings.github_token,
            timeout_seconds=settings.github_publication_timeout_seconds,
        )
    )
    return ProductRuntimeServiceWithGitHubPublication(
        catalog=catalog,
        evidence_store=evidence_store,
        dag_store=dag_store,
        provisioner=provisioner,
        workspace_resolver=resolver,
        dispatcher=dispatcher,
        publication_store=publication_store,
        github_publisher=github_publisher,
    )
