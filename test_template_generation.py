#!/usr/bin/env python3
"""Test script to isolate template generation datetime issue."""

import sys
import os

sys.path.insert(0, "src")


def test_template_generation():
    """Test template generation step by step."""
    try:
        print("1. Testing DI container...")
        from infrastructure.di.container import get_container

        container = get_container()
        print("   ✅ DI container works")

        print("2. Testing AWS handler factory...")
        from providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

        handler_factory = container.get(AWSHandlerFactory)
        print(f"   ✅ Handler factory: {handler_factory}")

        print("3. Testing template generation...")
        example_templates = handler_factory.generate_example_templates()
        print(f"   ✅ Generated {len(example_templates)} templates")

        print("4. Testing template objects...")
        for i, template in enumerate(example_templates):
            print(f"   Template {i}: {template.template_id}")
            print(f"     Type: {type(template)}")
            print(f"     Created at: {template.created_at} (type: {type(template.created_at)})")
            print(f"     Updated at: {template.updated_at} (type: {type(template.updated_at)})")

        print("5. Testing model_dump...")
        for i, template in enumerate(example_templates):
            try:
                template_dict = template.model_dump(exclude_none=True, mode="json")
                print(f"   ✅ Template {i} model_dump works")
                print(f"     Keys: {list(template_dict.keys())}")
                if "created_at" in template_dict:
                    print(
                        f"     created_at in dict: {template_dict['created_at']} (type: {type(template_dict['created_at'])})"
                    )
            except Exception as e:
                print(f"   ❌ Template {i} model_dump failed: {e}")
                return False

        print("6. Testing JSON serialization...")
        import json

        class DateTimeEncoder(json.JSONEncoder):
            def default(self, obj):
                from datetime import datetime

                if isinstance(obj, datetime):
                    return obj.isoformat()
                return super().default(obj)

        for i, template in enumerate(example_templates):
            try:
                template_dict = template.model_dump(exclude_none=True, mode="json")
                json.dumps(template_dict, cls=DateTimeEncoder)  # Test serialization but don't store
                print(f"   ✅ Template {i} JSON serialization works")
            except Exception as e:
                print(f"   ❌ Template {i} JSON serialization failed: {e}")
                print(f"     Template dict: {template_dict}")
                return False

        print("✅ All tests passed!")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    os.chdir("/tmp/orb-test")
    success = test_template_generation()
    sys.exit(0 if success else 1)
