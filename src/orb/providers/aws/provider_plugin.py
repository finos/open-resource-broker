"""AWS provider plugin — structured onboarding via :class:`ProviderPlugin`.

Implements all mandatory satellite accessors by delegating to the existing
factory functions and class references in :mod:`orb.providers.aws.registration`.
The legacy public functions in that module are kept as thin wrappers that
delegate to a module-level ``_aws_plugin`` singleton so back-compat is preserved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from orb.providers.base.provider_plugin import ProviderPlugin

if TYPE_CHECKING:
    pass


class AWSPlugin(ProviderPlugin):
    """Concrete :class:`ProviderPlugin` for the AWS provider.

    All satellite accessors use lazy imports so this module imports cleanly even
    when the optional AWS SDK dependencies are absent.
    """

    provider_name = "aws"

    # ------------------------------------------------------------------
    # Mandatory satellite accessors
    # ------------------------------------------------------------------

    def strategy_factory(self) -> Any:
        from orb.providers.aws.registration import create_aws_strategy

        return create_aws_strategy

    def config_factory(self) -> Any:
        from orb.providers.aws.registration import create_aws_config

        return create_aws_config

    def resolver_factory(self) -> Optional[Any]:
        from orb.providers.aws.registration import create_aws_resolver

        return create_aws_resolver

    def validator_factory(self) -> Optional[Any]:
        from orb.providers.aws.registration import create_aws_validator

        return create_aws_validator

    def strategy_class(self) -> Optional[type]:
        try:
            from orb.providers.aws.strategy.aws_provider_strategy import (
                AWSProviderStrategy,
            )

            return AWSProviderStrategy
        except ImportError:
            return None

    def default_api(self) -> Optional[str]:
        from orb.providers.aws.registration import _load_aws_default_api

        return _load_aws_default_api()

    def provider_settings_class(self) -> Optional[type]:
        try:
            from orb.providers.aws.configuration.config import AWSProviderConfig

            return AWSProviderConfig
        except ImportError:
            return None

    def template_dto_config(self) -> Any:
        try:
            from orb.providers.aws.domain.template.aws_template_dto_config import (
                AWSTemplateDTOConfig,
            )

            return AWSTemplateDTOConfig
        except ImportError:
            return None

    def template_class(self) -> Optional[type]:
        try:
            from orb.providers.aws.domain.template.aws_template_aggregate import (
                AWSTemplate,
            )

            return AWSTemplate
        except ImportError:
            return None

    def cli_spec(self) -> Any:
        try:
            from orb.providers.aws.cli.aws_cli_spec import AWSCLISpec

            return AWSCLISpec()
        except ImportError:
            return None

    def field_mapping(self) -> Any:
        try:
            from orb.providers.aws.scheduler.hostfactory_field_mapping import (
                AWSFieldMapping,
            )

            return AWSFieldMapping()
        except ImportError:
            return None

    def defaults_loader(self) -> Any:
        try:
            from orb.providers.aws.defaults_loader import AWSDefaultsLoader

            return AWSDefaultsLoader()
        except ImportError:
            return None

    def template_example_generator(self, container: Any) -> Any:
        try:
            from orb.domain.base.ports import LoggingPort
            from orb.providers.aws.adapters.template_example_generator_adapter import (
                AWSTemplateExampleGeneratorAdapter,
            )
            from orb.providers.aws.infrastructure.aws_handler_factory import (
                AWSHandlerFactory,
            )

            logger = container.get(LoggingPort)
            aws_handler_factory = AWSHandlerFactory(aws_client=None, logger=logger)  # type: ignore[arg-type]
            return AWSTemplateExampleGeneratorAdapter(aws_handler_factory=aws_handler_factory)
        except ImportError:
            return None

    # ------------------------------------------------------------------
    # Optional hook overrides
    # ------------------------------------------------------------------

    def register_auth_strategies(self, logger: Optional[Any] = None) -> None:
        """Register IAM and Cognito auth strategies with the auth registry."""
        from orb.providers.aws.registration import register_aws_auth_strategies

        register_aws_auth_strategies(logger)

    def register_additional_services(self, container: Any, logger: Optional[Any] = None) -> None:
        """Register AMICacheService, ImageResolver, and AWSTemplateAdapter with the DI container.

        Delegates to the existing :func:`~orb.providers.aws.registration.register_aws_services_with_di`
        function so that the established try/except-logger.warning behavioural
        contract is fully preserved.
        """
        from orb.providers.aws.registration import register_aws_services_with_di

        register_aws_services_with_di(container)

    def _do_initialize(self, logger: Optional[Any] = None) -> None:
        """Register AWS storage backends (DynamoDB and Aurora) after DefaultsLoader.

        Runs after all standard satellite registrations complete.  Preserves the
        idempotency guards already present in
        :func:`~orb.providers.aws.registration.initialize_aws_provider`.
        """
        try:
            from orb.infrastructure.storage.registry import get_storage_registry
            from orb.providers.aws.storage.registration import (
                register_aurora_storage,
                register_dynamodb_storage,
            )

            _storage_registry = get_storage_registry()
            if not _storage_registry.is_registered("dynamodb"):
                register_dynamodb_storage(_storage_registry, logger)
            if not _storage_registry.is_registered("aurora"):
                register_aurora_storage(_storage_registry, logger)
        except ImportError as exc:
            # Storage backend extras (boto3/sqlalchemy) not installed; skip silently.
            if logger:
                logger.debug(
                    "Skipping AWS storage registration — optional dependency absent: %s", exc
                )
            else:
                import logging as _logging

                _logging.getLogger(__name__).debug(
                    "Skipping AWS storage registration — optional dependency absent: %s", exc
                )

    # ------------------------------------------------------------------
    # register_services_with_di — override to delegate to the existing
    # function which carries the established try/except-logger.warning wrapper.
    # The base-class default would re-implement the same logic but the
    # explicit delegation keeps a single code path for behavioural parity.
    # ------------------------------------------------------------------

    def register_services_with_di(self, container: Any) -> None:
        """Register AWS utility services with the DI container.

        Delegates entirely to the existing
        :func:`~orb.providers.aws.registration.register_aws_services_with_di` so
        that the established ``try/except`` → ``logger.warning`` behavioural
        contract is preserved verbatim.  The template-example-generator is
        registered inside that function as well, so no double-registration occurs.
        """
        from orb.providers.aws.registration import register_aws_services_with_di

        register_aws_services_with_di(container)
