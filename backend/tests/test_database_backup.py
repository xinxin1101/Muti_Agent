from app.maintenance.database_backup import _redact


def test_backup_redacts_nested_sensitive_values() -> None:
    backup = _redact(
        {"project": {"api_key": "secret"}, "events": [{"message": "safe"}]}
    )

    assert backup["project"]["api_key"] == "[REDACTED]"
    assert backup["events"][0]["message"] == "safe"
