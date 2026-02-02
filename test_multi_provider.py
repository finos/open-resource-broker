#!/usr/bin/env python3
"""Test multi-provider functionality directly."""

import asyncio
import json
from config.managers.configuration_manager import ConfigurationManager
from config.schemas.provider_strategy_schema import ProviderInstanceConfig
from providers.registry import get_provider_registry
from providers.aws.registration import register_aws_provider_instance
from application.services.provider_capability_service import ProviderCapabilityService
from infrastructure.adapters.logging_adapter import LoggingAdapter

async def test_multi_provider():
    """Test multi-provider functionality."""
    print("=== Testing Multi-Provider Functionality ===")
    
    # Load config with explicit file
    config_manager = ConfigurationManager(config_file='config/test-multi-provider.json')
    raw_config = config_manager.get_raw_config()
    providers_data = raw_config['provider']['providers']
    
    print(f"Found {len(providers_data)} providers in config:")
    for p in providers_data:
        print(f"  {p['name']}: enabled={p['enabled']}, type={p['type']}")
    
    # Register enabled providers
    registry = get_provider_registry()
    enabled_providers = []
    
    for provider_data in providers_data:
        if provider_data['enabled']:
            provider_instance = ProviderInstanceConfig(**provider_data)
            print(f"\nRegistering: {provider_instance.name}")
            register_aws_provider_instance(provider_instance)
            enabled_providers.append(provider_instance)
    
    print(f"\nRegistry instances: {registry.get_registered_provider_instances()}")
    
    # Test provider capability service
    logger = LoggingAdapter()
    capability_service = ProviderCapabilityService(
        logger=logger,
        config_manager=config_manager,
        provider_registry=registry
    )
    
    # Test provider capabilities for each registered instance
    print(f"\nTesting provider capabilities:")
    
    for instance_name in registry.get_registered_provider_instances():
        try:
            # Get capabilities for this instance
            capabilities = capability_service._get_provider_capabilities(instance_name)
            if capabilities:
                print(f"  {instance_name}: {capabilities.supported_apis}")
                print(f"    Features: {capabilities.features}")
            else:
                print(f"  {instance_name}: No capabilities available")
        except Exception as e:
            print(f"  {instance_name}: ERROR - {e}")
    
    # Test template validation
    print(f"\n=== Testing Template Validation ===")
    from domain.template.template_aggregate import Template
    
    test_template = Template(
        template_id="test-template",
        provider_api="EC2Fleet",
        instance_type="t3.medium",
        max_instances=5
    )
    
    for instance_name in registry.get_registered_provider_instances():
        try:
            result = capability_service.validate_template_requirements(test_template, instance_name)
            print(f"{instance_name}: Valid={result.is_valid}, Errors={len(result.errors)}")
            if result.errors:
                for error in result.errors:
                    print(f"  ERROR: {error}")
        except Exception as e:
            print(f"{instance_name}: VALIDATION ERROR - {e}")
    
    # Test selection policy
    selection_policy = raw_config['provider'].get('selection_policy', 'FIRST_AVAILABLE')
    print(f"\nSelection policy: {selection_policy}")
    
    # Test handler overrides
    print("\n=== Testing Handler Overrides ===")
    for provider_data in providers_data:
        if provider_data['enabled'] and 'handler_overrides' in provider_data:
            provider_instance = ProviderInstanceConfig(**provider_data)
            effective_handlers = provider_instance.get_effective_handlers()
            print(f"{provider_instance.name} effective handlers:")
            for handler_name, handler_config in effective_handlers.items():
                if handler_config is None:
                    print(f"  {handler_name}: DISABLED")
                else:
                    print(f"  {handler_name}: {handler_config.handler_class}")
    
    print("\n=== Multi-Provider Test Complete ===")

if __name__ == "__main__":
    asyncio.run(test_multi_provider())