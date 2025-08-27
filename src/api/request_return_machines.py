import logging
from typing import List, Dict, Any
from src.models.provider.request import Request, RequestType
from src.database.database_handler import DatabaseHandler
from aws.aws_handler.aws_handler import AWSHandler
from src.helpers.logger import setup_logging

logger = setup_logging()


class RequestReturnMachines:
    """
    API to return machines back to the cloud provider.
    """

    def __init__(self, aws_handler: AWSHandler, db_handler: DatabaseHandler):
        """
        Initialize RequestReturnMachines with dependencies.

        :param aws_handler: Instance of AWSHandler to manage AWS operations.
        :param db_handler: Instance of DatabaseHandler to manage database operations.
        """
        self.aws_handler = aws_handler
        self.db_handler = db_handler

    def execute(self, input_data: Dict[str, Any], all_flag: bool = False) -> Dict[str, Any]:
        """
        Return specified machines or all machines from specific requests.

        :param input_data: A dictionary containing the input data for the request.
                           Example 1 (machines): {"machines": [{"machineId": "i-0abcd1234efgh5678"}]}
                           Example 2 (requests): {"requests": [{"requestId": "req-UUID"}]}
        :param all_flag: Whether to apply the return operation to all active requests.
        :return: A response with a return request ID or an error message.
        """
        try:
            logger.info("Processing return request.")

            if all_flag:
                return self._process_all_requests()

            if "machines" in input_data:
                machines = self._parse_machine_input(input_data)
                return self._process_machine_return(machines)
            elif "requests" in input_data:
                request_ids = self._parse_request_input(input_data)
                machines = self._get_machines_for_requests(request_ids)
                return self._process_machine_return(machines)
            else:
                raise ValueError("Invalid input. Provide 'machines', 'requests', or use the '--all' flag.")

        except Exception as e:
            logger.error(f"Error processing return request: {e}", exc_info=True)
            return {"error": str(e)}

    def _parse_machine_input(self, input_data: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Parse and validate machine-based input data.

        :param input_data: The raw input data.
                           Example: {"machines": [{"machineId": "i-0abcd1234efgh5678"}]}
        :return: List of machine dictionaries with `machineId`.
        """
        if not isinstance(input_data, dict) or "machines" not in input_data or not isinstance(input_data["machines"], list):
            raise ValueError("Input must include a 'machines' key with a list value.")

        machines = input_data["machines"]
        for machine in machines:
            if not isinstance(machine, dict) or "machineId" not in machine:
                raise ValueError("Each machine must be a dictionary with at least a 'machineId' key.")
        
        return machines

    def _parse_request_input(self, input_data: Dict[str, Any]) -> List[str]:
        """
        Parse and validate request-based input data.

        :param input_data: The raw input data.
                           Example: {"requests": [{"requestId": "req-UUID"}]}
        :return: List of valid request IDs.
        """
        if not isinstance(input_data, dict) or "requests" not in input_data or not isinstance(input_data["requests"], list):
            raise ValueError("Input must include a 'requests' key with a list value.")

        return [req["requestId"] for req in input_data["requests"] if isinstance(req, dict) and "requestId" in req]

    def _get_machines_for_requests(self, request_ids: List[str]) -> List[Dict[str, str]]:
        """
        Retrieve all machines associated with the given requests.

        :param request_ids: List of request IDs.
                            Example: ["req-UUID1", "req-UUID2"]
        :return: List of machine dictionaries with `machineId`.
                 Example: [{"machineId": "i-0abcd1234efgh5678"}]
        """
        machines = []
        for req_id in request_ids:
            db_machines = self.db_handler.get_machines_by_request_id(req_id)
            machines.extend([{"machineId": machine.machineId} for machine in db_machines])
        
        logger.info(f"Retrieved {len(machines)} machines for requests {request_ids}.")
        return machines

    def _process_all_requests(self) -> Dict[str, Any]:
        """
        Process the return operation for all active requests.

        :return: A response with the return request ID or an error message.
        """
        active_requests = self.db_handler.get_all_requests()
        
        if not active_requests:
            logger.warning("No active requests found.")
            return {"message": "No Active Requests.", "requestId": None}

        request_ids = [req.requestId for req in active_requests]
        
        machines = self._get_machines_for_requests(request_ids)
        
        return self._process_machine_return(machines)

    def _process_machine_return(self, machines: List[Dict[str, str]]) -> Dict[str, Any]:
         """
         Process a machine-based return operation.

         :param machines: List of machine dictionaries with `machineId`.
                          Example: [{"machineId": "i-0abcd1234efgh5678"}]
         :return: A response with a return request ID.
                  Example: {"message": "Delete VM success.", "requestId": "ret-UUID"}
         """
         # Generate a new return request ID
         return_request_id = Request.generate_request_id(request_type=RequestType.RETURN)

         # Fetch Machine objects from database using machine IDs
         machine_objects = [
             self.db_handler.get_machine(machine["machineId"]) for machine in machines if "machineId" in machine
         ]
         
         # Filter out None values (in case some machines are missing from the database)
         machine_objects = [m for m in machine_objects if m]

         # Call AWS handler to release hosts
         self.aws_handler.release_hosts(machine_objects)

         logger.info(f"Return request processed successfully. Request ID {return_request_id}")

         response = {"message": "Delete VM success.", "requestId": return_request_id}
         
         return response
