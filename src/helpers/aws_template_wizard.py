import json
from src.config.provider_template_manager import ProviderTemplateManager
from src.models.config.provider_template import ProviderTemplate
from src.helpers.logger import setup_logging

logger = setup_logging()

def interactive_template_creation():
    """Interactive wizard for creating AWS templates."""
    print("Welcome to the AWS Template Wizard!")
    template_manager = ProviderTemplateManager()

    # Ask user if they want to generate a launch template or just add a template entry
    choice = input("Do you want to (1) Generate a launch template or (2) Add a template entry? Enter 1 or 2: ")

    if choice == "1":
        template = create_launch_template()
    elif choice == "2":
        template = create_template_entry()
    else:
        print("Invalid choice. Exiting.")
        return

    # Save or validate the template
    print("\nTemplate Summary:")
    print(json.dumps(template, indent=4))
    save = input("Do you want to save this template? (yes/no): ").lower()
    
    if save == 'yes':
        try:
            provider_template = template_manager.create_provider_template(template)
            template_manager.add_template(provider_template)
            print(f"Template '{template['templateId']}' saved successfully.")
        except ValueError as e:
            print(f"Error saving template: {e}")
    else:
        print("Template not saved.")

def create_launch_template():
    """Create a full launch template."""
    # Logic for creating a full launch template remains unchanged
    pass

def create_template_entry():
    """Dynamically create a basic template entry using the ProviderTemplate model."""
    print("Creating a new template entry...")
    
    # Use the ProviderTemplate model to dynamically prompt for fields
    fields = ProviderTemplate.__annotations__  # Get all fields from the dataclass
    template = {}

    for field, field_type in fields.items():
        # Skip optional fields and additional options
        if field == "additionaloptions":
            continue

        # Prompt user for input
        value = input(f"Enter value for '{field}' ({field_type}): ").strip()

        # Convert value based on type hints
        if field_type == int:
            value = int(value) if value else None
        elif field_type == float:
            value = float(value) if value else None
        elif field_type == bool:
            value = value.lower() in ("true", "yes", "1")
        elif field_type == list or field_type == dict:
            try:
                value = json.loads(value) if value else None
            except json.JSONDecodeError:
                print(f"Invalid format for '{field}'. Skipping...")
                continue

        # Add to the template dictionary
        if value is not None:
            template[field] = value

    return template

if __name__ == "__main__":
    interactive_template_creation()
