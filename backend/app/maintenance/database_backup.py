from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.settings import Settings
from app.persistence.database import create_postgres_engine
from app.persistence.schema import expected_alembic_revision

_EXCLUDED_TABLES = frozenset({"project_credentials"})
_SENSITIVE_MARKERS = ("api_key", "authorization", "credential", "password", "secret", "token")


def _redact(value: Any, *, key: str = "") -> Any:
    """Return JSON-safe data without credentials or opaque secrets."""

    if any(marker in key.lower() for marker in _SENSITIVE_MARKERS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, bytes):
        return "[REDACTED_BINARY]"
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


def _quote_identifier(name: str) -> str:
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


async def create_logical_backup(*, output_root: Path | None = None) -> Path:
    """Export public schema and non-sensitive data without using shell database tools."""

    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("DEVFLOW_DATABASE_URL is required for a database backup")
    repository_root = Path(__file__).resolve().parents[3]
    root = output_root or repository_root / ".devflow" / "backups"
    backup_dir = root / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=False)
    engine = create_postgres_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            tables = tuple(
                row[0]
                for row in (
                    await connection.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' ORDER BY tablename"
                        )
                    )
                ).all()
                if row[0] not in _EXCLUDED_TABLES
            )
            columns = (
                await connection.execute(
                    text(
                        "SELECT table_name, column_name, data_type, is_nullable "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
                    )
                )
            ).mappings().all()
            constraints = (
                await connection.execute(
                    text(
                        "SELECT c.relname AS table_name, con.conname AS name, "
                        "pg_get_constraintdef(con.oid) AS definition "
                        "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' ORDER BY c.relname, con.conname"
                    )
                )
            ).mappings().all()
            data: dict[str, list[Any]] = {}
            for table in tables:
                select_rows = (
                    "SELECT to_jsonb(source) AS row "
                    f"FROM {_quote_identifier(table)} AS source"
                )
                result = await connection.execute(
                    text(select_rows)
                )
                data[table] = [_redact(row["row"]) for row in result.mappings().all()]
    finally:
        await engine.dispose()

    schema = {
        "revision": expected_alembic_revision(),
        "tables": tables,
        "columns": [_redact(dict(row)) for row in columns if row["table_name"] in tables],
        "constraints": [_redact(dict(row)) for row in constraints if row["table_name"] in tables],
    }
    schema_path = backup_dir / "schema.json"
    data_path = backup_dir / "data.json"
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    data_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "format": "devflow-logical-backup-v1",
        "schema_revision": schema["revision"],
        "excluded_tables": sorted(_EXCLUDED_TABLES),
        "table_row_counts": {table: len(rows) for table, rows in data.items()},
        "sha256": {
            "schema.json": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
            "data.json": hashlib.sha256(data_path.read_bytes()).hexdigest(),
        },
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return backup_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a redacted local DevFlow database backup.")
    parser.add_argument("--output-root", type=Path, default=None)
    options = parser.parse_args()
    backup_dir = asyncio.run(create_logical_backup(output_root=options.output_root))
    print(f"DevFlow backup created: {backup_dir}")


if __name__ == "__main__":
    main()
