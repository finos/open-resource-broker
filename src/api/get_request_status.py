import logging
from typing import Dict, Any, List
from src.database.database_handler import DatabaseHandler
from aws.aws_handler.aws_handler import AWSHandler
from src.models.provider.request import Request, RequestType, RequestStatus
from src.models.provider.machine import Machine, MachineStatus
from src.helpers.logger import setup_logging

logger = setup_logging()


class GetRequestStatus:
    """
    API to get the status of one or more requests, whether they are acquire or return requests.
    """

    def __init__(self, aws_handler: AWSHandler, db_handler: DatabaseHandler):
        """
        Initialize GetRequestStatus with dependencies.

        :param aws_handler: Instance of AWSHandler to manage AWS operations.
        :param db_handler: Instance of DatabaseHandler to manage database operations.
        """
        self.aws_handler = aws_handler
        self.db_handler = db_handler

    def execute(self, input_data: dict = None, all_flag: bool = False, long: bool = False) -> dict:
        """
        Get the status of one or more requests.

        :param input_data: A dictionary containing request IDs in various formats.
        :param all_flag: Whether to process all active requests.
        :param long: Whether to include all fields in the response.
        :return: A response with the request statuses and details or an error message.
        """
        try:
            logger.info("Processing request status.")

            if all_flag:
                return self._process_all_requests(long)

            parsed_data = self._parse_input_data(input_data)
            if not parsed_data:
                raise ValueError("Invalid input format. No valid 'requestId' found.")

            responses = []
            for request_id in parsed_data:
                logger.info(f"Getting status for Request ID {request_id}.")
                response = self._process_request(request_id, long)
                responses.append(response)

            return {"requests": responses}

        except Exception as e:
            logger.error(f"Error processing requests: {e}", exc_info=True)
            return {"error": str(e)}

    def _process_all_requests(self, long: bool) -> dict:
        """
        Process all active requests and return their statuses.

        :param long: Whether to include all fields in the response.
        :return: A dictionary containing statuses for all active requests.
        """
        try:
            all_requests = self.db_handler.get_all_requests()
            if not all_requests:
                logger.warning("No active requests found.")
                return {"message": "No Active Requests."}

            responses = []
            for request in all_requests:
                response = self._process_request(request.requestId, long)
                responses.append(response)

            return {"requests": responses}

        except Exception as e:
            logger.error(f"Error processing all requests: {e}", exc_info=True)
            return {"error": str(e)}

    def _parse_input_data(self, input_data: dict) -> List[str]:
        """
        Parse and extract request IDs from the input data.

        :param input_data: The raw input data.
        :return: A list of valid request IDs.
        """
        if isinstance(input_data, dict):
            if "requests" in input_data:
                requests = input_data["requests"]
                if isinstance(requests, list):
                    return [r["requestId"] for r in requests if isinstance(r, dict) and "requestId" in r]
                elif isinstance(requests, dict) and "requestId" in requests:
                    return [requests["requestId"]]
            elif "requestId" in input_data:
                return [input_data["requestId"]]

        raise ValueError("Invalid input format. No valid 'requestId' found.")

    def _process_request(self, request_id: str, long: bool) -> Dict[str, Any]:
        """
        Process a single request and return its status.

        :param request_id: The ID of the request to process.
        :param long: Whether to include all fields in the response.
        :return: A dictionary containing the request status and details.
        """
        try:
            logger.info(f"Processing Request ID {request_id}.")

            # Retrieve the specific request from the database
            current_request = self.db_handler.get_request(request_id)
            
            if not current_request:
                return {"requestId": request_id, "error": f"Request ID {request_id} not found in database."}

            # Convert dictionary to Request object if necessary
            if isinstance(current_request, dict):
                current_request = Request.from_dict(current_request)

            # Process based on the type of request
            if current_request.requestType == RequestType.ACQUIRE.value:
                return self._handle_acquire_request(current_request, long)
            elif current_request.requestType == RequestType.RETURN.value:
                return self._handle_return_request(current_request, long)
            else:
                return {"requestId": request_id, "error": f"Unknown request type '{current_request.requestType}'."}

        except Exception as e:
            logger.error(f"Error processing Request ID {request_id}: {e}", exc_info=True)
            return {"requestId": request_id, "error": str(e)}

    def _handle_acquire_request(self, acquire_request: Request, long: bool) -> Dict[str, Any]:
         """
         Handle status updates for an acquire (provisioning) operation.

         :param acquire_request: The Request object representing an acquire operation.
         :param long: Whether to include all fields in the response.
         :return: A response with updated statuses and details.
         """
         try:
             logger.info(f"Processing Acquire Request ID {acquire_request.requestId}.")

             # Call AWSHandler to get current statuses of machines
             machines = self.aws_handler.check_request_status(acquire_request)

             # Save updated machines to database
             for machine in machines:
                 logger.info(f"Processing machine {machine.machineId}.")
                 machine.requestId = acquire_request.requestId  # Link machine to acquire request
                 existing_machine = self.db_handler.get_machine(machine.machineId)

                 if not existing_machine:
                     self.db_handler.add_machine(machine)
                     logger.info(f"Added new machine {machine.machineId} to database.")
                 else:
                     self.db_handler.update_machine(machine)
                     logger.info(f"Updated existing machine {machine.machineId} in database.")

             # Update aggregated metrics at the request level
             related_machines = self.db_handler.get_machines_by_request_id(acquire_request.requestId)
             
             acquire_request.numRunning = sum(1 for m in related_machines if m.status == MachineStatus.RUNNING.value)
             acquire_request.numFailed = sum(1 for m in related_machines if (m.status != MachineStatus.RUNNING.value and m.status != MachineStatus.RETURNED.value))
             acquire_request.numReturned = sum(1 for m in related_machines if m.status == MachineStatus.RETURNED.value)

             # Save updated metrics back to database
             self.db_handler.update_request(acquire_request)

             return acquire_request.format_response(related_machines, long)

         except Exception as e:
             logger.error(f"Error handling Acquire Request ID {acquire_request.requestId}: {e}", exc_info=True)
             return {"requestId": acquire_request.requestId, "error": str(e)}

    def _handle_return_request(self, return_request: Request, long: bool) -> Dict[str, Any]:
         """
         Handle status updates for a return operation.

         :param return_request: The Request object representing a return operation.
         :param long: Whether to include all fields in the response.
         :return: A response with updated statuses and details.
         """
         try:
             logger.info(f"Processing Return Request ID {return_request.requestId}.")

             # Call AWSHandler to get current statuses of machines
             machines = self.aws_handler.check_request_status(return_request)

             # Save updated machines to database and assign the returnId
             for machine in machines:
                 machine.returnId = return_request.requestId  # Link machine to return request
                 existing_machine = self.db_handler.get_machine(machine.machineId)
                 
                 if not existing_machine:
                     self.db_handler.add_machine(machine)
                     logger.info(f"Added new machine {machine.machineId} to database.")
                 else:
                     self.db_handler.update_machine(machine)
                     logger.info(f"Updated existing machine {machine.machineId} in database.")

             # Update aggregated metrics at the request level
             related_machines = self.db_handler.get_machines_by_status(MachineStatus.RETURNED.value)
             
             return_request.numReturned += len(related_machines)

             # Save updated metrics back to database
             self.db_handler.update_request(return_request)

             return return_request.format_response(related_machines, long)

         except Exception as e:
             logger.error(f"Error handling Return Request ID {return_request.requestId}: {e}", exc_info=True)
             return {"requestId": return_request.requestId, "error": str(e)}
