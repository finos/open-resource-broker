#!/usr/bin/env python3

import sys
sys.path.insert(0, 'src')

def test_extension_registry():
    """Test the extension registry to reproduce the method error."""
    from domain.template.extensions import TemplateExtensionRegistry
    
    # Test the class method directly
    print("Testing TemplateExtensionRegistry.has_extension('aws'):")
    result = TemplateExtensionRegistry.has_extension('aws')
    print(f"Result: {result}")
    
    # Test assignment like in template defaults service
    print("\nTesting assignment like in template defaults service:")
    extension_registry = TemplateExtensionRegistry
    print(f"extension_registry type: {type(extension_registry)}")
    print(f"extension_registry.has_extension type: {type(extension_registry.has_extension)}")
    
    # Test the method call
    try:
        result = extension_registry.has_extension('aws')
        print(f"extension_registry.has_extension('aws'): {result}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

def test_template_defaults_service():
    """Test the template defaults service initialization."""
    try:
        from application.services.template_defaults_service import TemplateDefaultsService
        from domain.template.extensions import TemplateExtensionRegistry
        
        # Mock the required dependencies
        class MockConfigManager:
            def get_template_config(self):
                return {}
            def get_provider_config(self):
                return type('obj', (object,), {'providers': []})()
        
        class MockLogger:
            def debug(self, msg, *args): pass
            def warning(self, msg, *args): pass
            def info(self, msg, *args): pass
            def error(self, msg, *args): pass
        
        # Create service instance
        service = TemplateDefaultsService(
            config_manager=MockConfigManager(),
            logger=MockLogger(),
            extension_registry=None  # This should default to TemplateExtensionRegistry
        )
        
        print(f"service.extension_registry type: {type(service.extension_registry)}")
        print(f"service.extension_registry: {service.extension_registry}")
        
        # Test the problematic call
        try:
            result = service.extension_registry.has_extension('aws')
            print(f"service.extension_registry.has_extension('aws'): {result}")
        except Exception as e:
            print(f"Error in service: {e}")
            import traceback
            traceback.print_exc()
            
    except Exception as e:
        print(f"Error creating service: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("=== Testing Extension Registry ===")
    test_extension_registry()
    
    print("\n=== Testing Template Defaults Service ===")
    test_template_defaults_service()