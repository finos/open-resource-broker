import logging
from typing import Dict, Any
from src.models.provider.request import Request, RequestType, RequestStatus
from src.config.provider_template_manager import ProviderTemplateManager
from src.database.database_handler import DatabaseHandler
from aws.aws_handler.aws_handler import AWSHandler
from src.helpers.logger import setup_logging

logger = setup_logging()


class RequestMachines:
    """
    API to request machines based on a specific template.
    """

    def __init__(self, aws_handler: AWSHandler, db_handler: DatabaseHandler, template_manager: ProviderTemplateManager):
        """
        Initialize RequestMachines with dependencies.

        :param aws_handler: Instance of AWSHandler to manage AWS operations.
        :param db_handler: Instance of DatabaseHandler to manage database operations.
        :param template_manager: Instance of ProviderTemplateManager to manage templates.
        """
        self.aws_handler = aws_handler
        self.db_handler = db_handler
        self.template_manager = template_manager

    def execute(self, input_data: dict) -> Dict[str, Any]:
        """
        Request machines based on a specific template.

        :param input_data: A dictionary containing the input data for the request.
                           Example: {"templateId": "RunInstances", "numMachines": 2}
        :return: A response with request ID and status or an error message.
        """
        try:
            logger.info("Processing request for machines.")

            # Parse and validate input data
            parsed_data = self._parse_input_data(input_data)

            # Validate the template and machine count
            template = self.template_manager.get_template(parsed_data["templateId"])
            if not template:
                raise ValueError(f"Template with ID '{parsed_data['templateId']}' not found.")
            if parsed_data["machineCount"] > template.maxNumber:
                raise ValueError(
                    f"Requested machine count ({parsed_data['machineCount']}) exceeds maximum allowed "
                    f"({template.maxNumber}) for template {parsed_data['templateId']}."
                )

            # Generate a unique request ID for an acquire request
            request_id = Request.generate_request_id(request_type=RequestType.ACQUIRE)
            logger.info(f"Generated request ID: {request_id}")

            # Create a Request object and save it in the database
            request = Request(
                requestId=request_id,
                requestType=RequestType.ACQUIRE,
                numRequested=parsed_data["machineCount"],
                templateId=parsed_data["templateId"],
                status=RequestStatus.RUNNING,
                awsHandler=template.awsHandler,
                message="Request initiated."
            )
            self.db_handler.add_request(request)
            logger.info(f"Request {request_id} saved to database.")

            # Process the acquire request by calling AWS but do not save machines yet
            return self._process_request(request)

        except Exception as e:
            logger.error(f"Error requesting machines: {e}", exc_info=True)
            return {"error": str(e)}

    def _parse_input_data(self, input_data: dict) -> Dict[str, Any]:
        """
        Parse and validate input data for requesting machines.

        :param input_data: The raw input data.
                           Example: {"template": {"templateId": "RunInstances", "numMachines": 2}}
        :return: Parsed and validated input data.
                 Example: {"templateId": "RunInstances", "machineCount": 2}
        """
        if not isinstance(input_data, dict):
            raise ValueError("Input data must be a dictionary.")

        if "template" not in input_data or not isinstance(input_data["template"], dict):
            raise ValueError("Input data must include a 'template' key with a dictionary value.")

        template_data = input_data["template"]
        if "templateId" not in template_data or "numMachines" not in template_data:
            raise ValueError("Input data must include 'templateId' and 'numMachines' within the 'template' dictionary.")

        try:
            machine_count = int(template_data["numMachines"])
        except ValueError:
            raise ValueError("'numMachines' must be an integer.")

        return {
            "templateId": template_data["templateId"],
            "machineCount": machine_count,
        }

    def _process_request(self, request: Request) -> Dict[str, Any]:
        """
        Process an acquire request by initiating provisioning via AWS.

        :param request: The Request object representing the acquire operation.
        :return: A response with the request ID and status.
        """
        try:
            logger.info(f"Processing acquire request {request.requestId}.")

            # Call AWSHandler to initiate provisioning (but do not save machines yet)
            aws_response = self.aws_handler.initiate_provisioning(request)

            # Update the request status in the database based on AWS response
            if aws_response.status == RequestStatus.RUNNING:
                request.message = "AWS provisioning initiated successfully."
                self.db_handler.update_request(request)
                logger.info(f"Request {request.requestId} updated to 'running' in database.")
                return {
                    "requestId": request.requestId,
                    "status": request.status.value,
                    "message": request.message,
                }
            else:
                # Handle failure case
                request.status = RequestStatus.COMPLETE_WITH_ERRORS
                request.message = f"AWS provisioning failed. Error: {aws_response.message}"
                self.db_handler.update_request(request)
                logger.error(f"Request {request.requestId} failed. Error: {aws_response.message}")
                return {
                    "requestId": request.requestId,
                    "status": request.status.value,
                    "message": request.message,
                    "error": aws_response.message,
                }

        except Exception as e:
            logger.error(f"Error processing acquire request {request.requestId}: {e}", exc_info=True)
            return {"error": str(e)}
