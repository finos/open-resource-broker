import argparse
import json
import sys

from src.api.get_available_templates import GetAvailableTemplates
from src.api.request_machines import RequestMachines
from src.api.request_return_machines import RequestReturnMachines
from src.api.get_request_status import GetRequestStatus
# from src.api.get_return_requests import GetReturnRequests
from src.config.provider_config_manager import ProviderConfigManager
from src.config.provider_template_manager import ProviderTemplateManager
from src.database.database_handler import DatabaseHandler
from aws.aws_handler.aws_handler import AWSHandler
from src.helpers.utils import load_json_data
from helpers.aws_template_wizard import interactive_template_creation
from src.helpers.logger import setup_logging


def main():
    """
    Main entry point for the AWS Host Factory Plugin.
    """
    parser = argparse.ArgumentParser(description="AWS Host Factory Plugin for IBM Spectrum Symphony")

    # Define supported actions
    parser.add_argument(
        "action",
        choices=[
            "getAvailableTemplates",
            "requestMachines",
            "requestReturnMachines",
            "getRequestStatus",
            "getReturnRequests",
            "templateWizard",
        ],
        help="Action to perform."
    )

    # Define optional arguments for input data and file paths
    parser.add_argument("--data", help="JSON string input.")
    parser.add_argument("-f", "--file", help="Path to JSON file input.")
    
    # Additional flags from old project structure
    parser.add_argument("--all", action="store_true", help="Apply action to all templates or requests.")
    parser.add_argument("--clean", action="store_true", help="Clean up all resources and database entries.")
    parser.add_argument("--long", action="store_true", help="Display detailed responses with all the information available.")
    
    args = parser.parse_args()

    # Set up logging
    logger = setup_logging()

    try:
        # Initialize managers and handlers
        config_manager = ProviderConfigManager()
        template_manager = ProviderTemplateManager()
        db_handler = DatabaseHandler()
        aws_handler = AWSHandler()

        # Load input data (optional)
        input_data = None
        if args.data or args.file:
            input_data = load_json_data(json_str=args.data, json_file=args.file)

        # Handle actions based on user input
        if args.action == "getAvailableTemplates":
            api = GetAvailableTemplates(template_manager)
            response = api.execute(long=args.long)
        
        elif args.action == "requestMachines":
            if not input_data:
                raise ValueError("Input data is required for 'requestMachines'.")
            api = RequestMachines(aws_handler, db_handler, template_manager)
            response = api.execute(input_data, long=args.long)
        
        elif args.action == "requestReturnMachines":
            if not input_data and not args.all:
                raise ValueError("Input data or the '--all' flag is required for 'requestReturnMachines'.")
            api = RequestReturnMachines(aws_handler, db_handler)
            response = api.execute(input_data, all_flag=args.all)
        
        elif args.action == "getRequestStatus":
            if not input_data and not args.all:
                raise ValueError("Input data or the '--all' flag is required for 'getRequestStatus'.")
            api = GetRequestStatus(aws_handler, db_handler)
            response = api.execute(input_data, all_flag=args.all, long=args.long)
        
        elif args.action == "getReturnRequests":
            api = GetReturnRequests(db_handler)
            response = api.execute(long=args.long)
        
        elif args.action == "templateWizard":
            interactive_template_creation()
            return

        else:
            raise ValueError(f"Unsupported action: {args.action}")

        # Print the response as JSON
        print(json.dumps(response, indent=2))

    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
