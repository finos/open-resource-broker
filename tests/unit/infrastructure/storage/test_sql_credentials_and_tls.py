"""SQL credentials stay secret and PostgreSQL connects through the shipped driver."""

from __future__ import annotations

import ssl

import pytest
from pydantic import SecretStr, ValidationError

from orb.config.schemas.storage_schema import SqlStrategyConfig
from orb.infrastructure.storage.components.postgres_tls import (
    pg8000_ssl_context,
    postgres_connect_args,
)
from orb.infrastructure.storage.sql.registration import _build_connection_string, _build_url

pytestmark = pytest.mark.unit

PASSWORD = "p@ss/w:rd#1%"


def _cfg(**overrides) -> SqlStrategyConfig:
    values = {
        "type": "postgresql",
        "host": "db.example",
        "port": 5432,
        "name": "orb",
        "username": "us@er",
        "password": PASSWORD,
    }
    values.update(overrides)
    return SqlStrategyConfig(**values)


def test_password_is_a_secret_everywhere_it_could_be_printed():
    cfg = _cfg()
    assert isinstance(cfg.password, SecretStr)
    assert PASSWORD not in repr(cfg)
    assert PASSWORD not in str(cfg)
    assert PASSWORD not in str(cfg.model_dump())
    assert PASSWORD not in cfg.model_dump_json()


def test_validation_error_does_not_echo_the_password():
    with pytest.raises(ValidationError) as exc:
        _cfg(port="not-a-port")
    assert PASSWORD not in str(exc.value)


def test_unexpanded_placeholder_password_is_still_rejected():
    with pytest.raises(ValidationError):
        _cfg(password="${ORB_DB_PASSWORD_UNSET}")


def test_failed_connection_error_does_not_contain_the_password():
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from orb.infrastructure.storage.sql.registration import _create_engine_for

    cfg = _cfg(host="127.0.0.1", port=1, sslmode="disable")
    engine = _create_engine_for(cfg)
    try:
        with pytest.raises(SQLAlchemyError) as exc:
            with engine.connect() as conn:
                conn.execute(text("select 1"))
    finally:
        engine.dispose()
    assert PASSWORD not in str(exc.value)


def test_url_uses_the_installed_postgres_driver():
    pytest.importorskip("pg8000")
    url = _build_url(_cfg())
    assert url.drivername == "postgresql+pg8000"
    assert url.get_dialect().driver == "pg8000"


def test_credentials_with_reserved_characters_round_trip():
    from sqlalchemy.engine import make_url

    rendered = _build_connection_string(_cfg())
    parsed = make_url(rendered)
    assert parsed.username == "us@er"
    assert parsed.password == PASSWORD
    assert parsed.host == "db.example"
    assert parsed.port == 5432
    assert parsed.database == "orb"


def test_default_sslmode_is_require():
    assert _cfg().sslmode == "require"


def test_invalid_sslmode_is_rejected():
    with pytest.raises(ValidationError):
        _cfg(sslmode="prefer")


def test_disable_requests_no_tls():
    assert postgres_connect_args("disable") == {}


def test_require_encrypts_without_verifying():
    ctx = pg8000_ssl_context("require")
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_verify_ca_checks_the_chain_but_not_the_host():
    ctx = pg8000_ssl_context("verify-ca")
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is False


def test_verify_full_checks_chain_and_host():
    ctx = pg8000_ssl_context("verify-full")
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_unset_sslmode_uses_the_default():
    assert postgres_connect_args(None)["ssl_context"].verify_mode == ssl.CERT_NONE


def test_auto_upgrade_takes_the_migration_lock(tmp_path, monkeypatch):
    """The in-process upgrade holds the migration lock while it runs migrations."""
    from unittest.mock import MagicMock

    from sqlalchemy import create_engine

    from orb.infrastructure.storage.sql import migration_lock as lock_module
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    entered: list[str] = []
    real = lock_module.migration_lock

    def spy(connection):
        entered.append(connection.dialect.name)
        return real(connection)

    monkeypatch.setattr(lock_module, "migration_lock", spy)
    script_dir = MagicMock()
    script_dir._upgrade_revs.return_value = []
    engine = create_engine(f"sqlite:///{tmp_path / 'u.db'}")
    try:
        strategy = SQLStorageStrategy.__new__(SQLStorageStrategy)
        strategy.logger = MagicMock()
        strategy._upgrade_to_head(engine, script_dir)
    finally:
        engine.dispose()
    assert entered == ["sqlite"]
