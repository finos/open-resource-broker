"""A secret that still reads ``${VAR}`` after expansion is an error, on every path.

Environment expansion leaves ``${VAR}`` untouched when ``VAR`` is not set; without
this check the placeholder text itself would be used as the secret. The checks run
on the expanded value, so a secret whose variable *is* set must pass everywhere,
including the startup validator, which reads the file straight from disk.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from orb.config.schemas.storage_schema import SqlStrategyConfig
from orb.config.utils.env_expansion import expand_config_env_vars, reject_unexpanded_placeholder
from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.validation.startup_validator import StartupValidator

SECRET = "sup3r-s3cret-value-0123456789abcdef0123456789"
UNSET = "ORB_TEST_SECRET_NOT_SET"
SET = "ORB_TEST_SECRET_IS_SET"


@pytest.fixture(autouse=True)
def _variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(UNSET, raising=False)
    monkeypatch.setenv(SET, SECRET)
    # Provider settings classes are registered when a provider initialises
    from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
    from orb.providers.aws.configuration.config import AWSProviderConfig

    monkeypatch.setitem(ProviderSettingsRegistry._settings_classes, "aws", AWSProviderConfig)


class _Console:
    """Records everything the validator prints."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def _add(self, message: str, *_a: Any, **_k: Any) -> None:
        self.lines.append(message)

    info = success = error = warning = command = _add

    def separator(self, *_a: Any, **_k: Any) -> None:
        return None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _default_config() -> dict[str, Any]:
    return json.loads(resources.files("orb.config").joinpath("default_config.json").read_text())


def _write(tmp_path: Path, **sections: Any) -> str:
    config = _default_config()
    for dotted, value in sections.items():
        node = config
        *parents, leaf = dotted.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return str(path)


def _bearer(tmp_path: Path, secret: str) -> str:
    return _write(
        tmp_path,
        **{
            "server.auth": {
                "enabled": True,
                "strategy": "bearer_token",
                "bearer_token": {"secret_key": secret},
            }
        },
    )


def _sql(tmp_path: Path, password: str) -> str:
    return _write(
        tmp_path,
        **{
            "storage.strategy": "sql",
            "storage.sql_strategy": {
                "type": "postgresql",
                "host": "db.example",
                "port": 5432,
                "name": "orb",
                "username": "orb",
                "password": password,
            },
        },
    )


def _validate(path: str) -> tuple[_Console, StartupValidator]:
    console = _Console()
    validator = StartupValidator(config_path=path, console=console)  # type: ignore[arg-type]
    return console, validator


@pytest.mark.unit
class TestHelper:
    def test_value_passes_through(self) -> None:
        assert reject_unexpanded_placeholder("plain", "x") == "plain"
        assert reject_unexpanded_placeholder(None, "x") is None

    def test_bare_dollar_words_may_be_part_of_a_secret(self) -> None:
        assert reject_unexpanded_placeholder("pa$$word$HOME", "x") == "pa$$word$HOME"

    def test_braced_placeholder_is_rejected_naming_setting_and_variable_only(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            reject_unexpanded_placeholder("prefix-secret-${MISSING_VAR}-tail", "storage.password")

        message = str(exc_info.value)
        assert "storage.password" in message and "MISSING_VAR" in message
        assert "prefix-secret" not in message and "tail" not in message

    def test_only_the_variable_name_is_echoed_not_what_follows_it(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            reject_unexpanded_placeholder("${MISSING_VAR:-hunter2}", "x")

        assert "MISSING_VAR" in str(exc_info.value)
        assert "hunter2" not in str(exc_info.value)

    def test_a_placeholder_without_a_plain_name_names_no_variable(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            reject_unexpanded_placeholder("${:hunter2}", "x")

        assert "hunter2" not in str(exc_info.value)

    def test_expansion_then_check_accepts_a_set_variable(self) -> None:
        expanded = expand_config_env_vars({"a": f"${{{SET}}}"})

        assert reject_unexpanded_placeholder(expanded["a"], "a") == SECRET


@pytest.mark.unit
class TestSettingsRejectAfterExpansion:
    def test_bearer_secret(self) -> None:
        from pydantic import ValidationError

        from orb.config.schemas.server_schema import BearerTokenAuthSubConfig

        BearerTokenAuthSubConfig(secret_key=SECRET)  # type: ignore[arg-type]
        with pytest.raises(ValidationError) as exc_info:
            BearerTokenAuthSubConfig(secret_key=f"{SECRET}${{{UNSET}}}")  # type: ignore[arg-type]

        assert UNSET in str(exc_info.value) and SECRET not in str(exc_info.value)

    def test_sql_password(self) -> None:
        from pydantic import ValidationError

        SqlStrategyConfig(password=SECRET)
        with pytest.raises(ValidationError) as exc_info:
            SqlStrategyConfig(password=f"{SECRET}${{{UNSET}}}")

        assert UNSET in str(exc_info.value) and SECRET not in str(exc_info.value)

    @pytest.mark.parametrize("field", ["secret_access_key", "session_token"])
    def test_aws_credentials(self, field: str) -> None:
        from pydantic import ValidationError

        from orb.providers.aws.configuration.config import AWSProviderConfig

        AWSProviderConfig(**{field: SECRET})  # type: ignore[arg-type]
        with pytest.raises(ValidationError) as exc_info:
            AWSProviderConfig(**{field: f"{SECRET}${{{UNSET}}}"})  # type: ignore[arg-type]

        assert UNSET in str(exc_info.value) and SECRET not in str(exc_info.value)

    def test_other_settings_may_keep_a_placeholder(self) -> None:
        # Only secret settings are checked; a stray ${...} elsewhere is not this rule's business
        assert SqlStrategyConfig(host="${NOT_A_SECRET}", password="x").host == "${NOT_A_SECRET}"


@pytest.mark.unit
class TestStartupValidatorPath:
    """The validator reads the file as written and must expand it like the loader."""

    @pytest.mark.parametrize("builder", [_bearer, _sql], ids=["bearer-secret", "sql-password"])
    def test_a_set_variable_passes_and_is_expanded(self, tmp_path: Path, builder: Any) -> None:
        console, validator = _validate(builder(tmp_path, f"${{{SET}}}"))

        validator._validate_critical()  # must not exit

        assert validator.app_config is not None
        assert SECRET not in console.text

    def test_the_expanded_value_is_what_gets_validated(self, tmp_path: Path) -> None:
        _, validator = _validate(_bearer(tmp_path, f"${{{SET}}}"))
        validator._validate_critical()

        assert validator.app_config is not None
        bearer = validator.app_config.server.auth.bearer_token
        assert bearer is not None and bearer.secret_key.get_secret_value() == SECRET

    @pytest.mark.parametrize(
        ("builder", "setting"),
        [(_bearer, "server.auth.bearer_token.secret_key"), (_sql, "storage.sql_strategy.password")],
        ids=["bearer-secret", "sql-password"],
    )
    def test_an_unset_variable_stops_startup_naming_setting_and_variable(
        self, tmp_path: Path, builder: Any, setting: str
    ) -> None:
        console, validator = _validate(builder(tmp_path, f"{SECRET}-${{{UNSET}}}"))

        with pytest.raises(SystemExit) as exit_info:
            validator.validate_startup()

        assert exit_info.value.code == 1
        assert setting in console.text and UNSET in console.text
        assert SECRET not in console.text

    def test_a_secret_with_no_placeholder_is_unaffected(self, tmp_path: Path) -> None:
        _, validator = _validate(_bearer(tmp_path, SECRET))

        validator._validate_critical()

        assert validator.app_config is not None


@pytest.mark.unit
class TestLoaderPath:
    def _manager(self, **overrides: Any) -> Any:
        from orb.config.manager import ConfigurationManager

        config = _default_config()
        for dotted, value in overrides.items():
            node = config
            *parents, leaf = dotted.split(".")
            for part in parents:
                node = node.setdefault(part, {})
            node[leaf] = value
        return ConfigurationManager(config_dict=config)

    def test_set_variable_is_expanded_in_the_application_config(self) -> None:
        manager = self._manager(**{"storage.sql_strategy.password": f"${{{SET}}}"})

        assert manager.app_config.storage.sql_strategy.password == SECRET

    def test_unset_variable_fails_loading_without_echoing_the_secret(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        manager = self._manager(**{"storage.sql_strategy.password": f"{SECRET}${{{UNSET}}}"})

        with pytest.raises(ConfigurationError) as exc_info:
            _ = manager.app_config

        # The loader reports the validation failure in its log; the secret is in neither
        assert "storage.sql_strategy.password" in caplog.text and UNSET in caplog.text
        assert SECRET not in caplog.text and SECRET not in repr(exc_info.value.__dict__)

    @pytest.mark.parametrize("field", ["secret_access_key", "session_token"])
    def test_aws_credentials_through_the_provider_section(
        self, field: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        from orb.providers.aws.configuration.config import AWSProviderConfig

        providers = [
            {"name": "aws-main", "type": "aws", "enabled": True, "config": {field: "${%s}"}}
        ]
        ok = self._manager(
            provider={"providers": [dict(providers[0], config={field: f"${{{SET}}}"})]}
        )
        bad = self._manager(
            provider={"providers": [dict(providers[0], config={field: f"{SECRET}${{{UNSET}}}"})]}
        )

        loaded = ok.get_typed(AWSProviderConfig)
        value = getattr(loaded, field)
        assert (value.get_secret_value() if hasattr(value, "get_secret_value") else value) == SECRET
        with pytest.raises(ConfigurationError) as exc_info:
            bad.get_typed(AWSProviderConfig)
        assert SECRET not in repr(exc_info.value.__dict__)
        assert SECRET not in caplog.text and UNSET in caplog.text


@pytest.mark.unit
class TestPasswordsStayOutOfReprs:
    def test_sql_password_is_not_in_repr(self) -> None:
        config = SqlStrategyConfig(password=SECRET)

        assert SECRET not in repr(config) and SECRET not in str(config)
        assert config.password == SECRET  # still readable by the code that connects

    def test_aurora_password_is_not_in_repr(self) -> None:
        from orb.providers.aws.storage.config import AuroraSqlStrategyConfig

        config = AuroraSqlStrategyConfig(  # type: ignore[call-arg]
            type="mysql", host="db", cluster_endpoint="db", port=3306, name="orb", password=SECRET
        )

        assert SECRET not in repr(config) and SECRET not in str(config)
