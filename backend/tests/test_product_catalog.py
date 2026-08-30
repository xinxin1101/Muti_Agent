from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from app.api.catalog import PostgresProductCatalog
from app.models import TaskContract
from app.persistence import PostgresEvidenceStore


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for product catalog tests")
    pytest.skip("product catalog test requires DEVFLOW_DATABASE_URL")


def _task() -> TaskContract:
    return TaskContract(
        task_id="catalog-task",
        objective="Exercise bounded product queries.",
        readable_files=["backend/app/**"],
        writable_files=["backend/app/api/**"],
        readonly_files=["backend/tests/**"],
        acceptance_criteria=["Product queries reflect accepted persistence rows."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def test_product_catalog_lists_projects_and_runs() -> None:
    asyncio.run(_exercise_catalog())


async def _exercise_catalog() -> None:
    database_url = _database_url()
    store = PostgresEvidenceStore.from_url(database_url)
    catalog = PostgresProductCatalog.from_url(database_url)
    repository_url = f"https://example.com/{uuid4()}.git"
    project_id = await store.ensure_project(
        repository_url=repository_url,
        default_branch="main",
    )
    run_id = await store.start_run(
        project_id=project_id,
        tasks=(_task(),),
        base_commit="a" * 40,
    )

    try:
        projects = await catalog.list_projects()
        project = next(item for item in projects if item.project_id == project_id)
        assert project.repository_url == repository_url
        assert project.run_count == 1
        assert project.provision_status == "READY"
        assert project.provision_error_code is None

        runs = await catalog.list_runs(project_id=project_id)
        run = next(item for item in runs if item.run_id == run_id)
        assert run.task_count == 1
        assert run.status == "RUNNING"
    finally:
        await catalog.dispose()
        await store.dispose()
