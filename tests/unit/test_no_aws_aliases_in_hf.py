"""TDD tests: PROVIDER_API_ALIASES must live in AWSProviderStrategy, not hostfactory_strategy."""

import inspect


class TestNoAliasesInHFSource:
    """Assert that hostfactory_strategy.py no longer owns the alias dict or EC2Fleet default."""

    def _hf_source(self) -> str:
        import orb.infrastructure.scheduler.hostfactory.hostfactory_strategy as mod

        return inspect.getsource(mod)

    def test_no_provider_api_aliases_dict_in_hf(self):
        assert "PROVIDER_API_ALIASES" not in self._hf_source()

    def test_no_hardcoded_ec2fleet_default_in_hf(self):
        # "EC2Fleet" must not appear as a fallback default string literal outside comments
        source = self._hf_source()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert '"EC2Fleet"' not in stripped and "'EC2Fleet'" not in stripped, (
                f"Hardcoded EC2Fleet default found in hostfactory_strategy.py: {line!r}"
            )


class TestResolveApiAliasOnBaseStrategy:
    """resolve_api_alias must exist on ProviderStrategy with identity default."""

    def test_method_exists_on_base(self):
        from orb.providers.base.strategy.provider_strategy import ProviderStrategy

        assert hasattr(ProviderStrategy, "resolve_api_alias"), (
            "ProviderStrategy must define resolve_api_alias"
        )

    def test_base_default_is_identity(self):
        """The base default returns the input unchanged (passthrough)."""
        from unittest.mock import MagicMock

        from orb.providers.base.strategy.provider_strategy import ProviderStrategy

        # ProviderStrategy is abstract; use a minimal concrete subclass
        class _Concrete(ProviderStrategy):
            @property
            def provider_type(self):
                return "test"

            def initialize(self):
                return True

            async def execute_operation(self, op):
                pass

            def get_capabilities(self):
                pass

            def check_health(self):
                pass

            def generate_provider_name(self, config):
                return ""

            def parse_provider_name(self, name):
                return {}

            def get_provider_name_pattern(self):
                return ""

            def cleanup(self):
                pass

        from orb.infrastructure.interfaces.provider import BaseProviderConfig

        cfg = MagicMock(spec=BaseProviderConfig)
        instance = _Concrete(cfg)
        assert instance.resolve_api_alias("AutoScalingGroup") == "AutoScalingGroup"
        assert instance.resolve_api_alias("anything") == "anything"


class TestAWSProviderStrategyAliases:
    """AWSProviderStrategy.resolve_api_alias must map the known aliases."""

    def _make_aws_strategy(self):
        from unittest.mock import MagicMock

        from orb.providers.aws.configuration.config import AWSProviderConfig
        from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        config = AWSProviderConfig(region="us-east-1")
        logger = MagicMock()
        return AWSProviderStrategy(config=config, logger=logger)

    def test_autoscalinggroup_maps_to_asg(self):
        assert self._make_aws_strategy().resolve_api_alias("AutoScalingGroup") == "ASG"

    def test_lowercase_autoscalinggroup_maps_to_asg(self):
        assert self._make_aws_strategy().resolve_api_alias("autoscalinggroup") == "ASG"

    def test_lowercase_asg_maps_to_asg(self):
        assert self._make_aws_strategy().resolve_api_alias("asg") == "ASG"

    def test_unknown_value_is_passthrough(self):
        assert self._make_aws_strategy().resolve_api_alias("EC2Fleet") == "EC2Fleet"
        assert self._make_aws_strategy().resolve_api_alias("RunInstances") == "RunInstances"
