import boto3
from typing import Any, Dict, List
from datetime import datetime, timezone
from ..general.base_aws_handler import BaseAWSHandler
from ...models.provider.request import Request, RequestStatus
from .odcr_model import OnDemandCapacityReservation
from ...database.database_handler import DatabaseHandler
from src.helpers.logger import setup_logging

logger = setup_logging()


class ODCRHandler(BaseAWSHandler):
    """
    Handler for managing On-Demand Capacity Reservations (ODCRs).
    """

    def __init__(self, region_name: str, db_handler: DatabaseHandler):
        self.ec2_client = boto3.client("ec2", region_name=region_name)
        self.db_handler = db_handler

    def acquire_hosts(self, request: Request) -> Request:
        """
        Acquire hosts by creating an On-Demand Capacity Reservation.
        """
        try:
            self.validate_request(request)
            reservation_config = self.get_reservation_config(request)

            response = self.ec2_client.create_capacity_reservation(**reservation_config)
            reservation = OnDemandCapacityReservation.fromDescribeCapacityReservations(response)

            request.resourceId = reservation.capacityReservationId
            request.update_status(RequestStatus.RUNNING, "Capacity Reservation created successfully.")

            # Persist the request in the database
            self.db_handler.add_or_update_request(request)

            return request

        except boto3.exceptions.Boto3Error as e:
            raise RuntimeError(f"Failed to create Capacity Reservation: {str(e)}")

    def release_hosts(self, request: Request) -> Request:
        """
        Release hosts by canceling an On-Demand Capacity Reservation.
        """
        try:
            if not request.resourceId:
                raise ValueError("No Capacity Reservation ID found in the request.")

            self.ec2_client.cancel_capacity_reservation(CapacityReservationId=request.resourceId)

            request.update_status(RequestStatus.COMPLETE, "Capacity Reservation canceled successfully.")

            # Update the request in the database
            self.db_handler.update_request(request)

            return request

        except boto3.exceptions.Boto3Error as e:
            raise RuntimeError(f"Failed to cancel Capacity Reservation: {str(e)}")

    def check_request_status(self, request: Request) -> Request:
        """
        Check the status of an On-Demand Capacity Reservation.
        """
        try:
            if not request.resourceId:
                raise ValueError("No Capacity Reservation ID found in the request.")

            paginator = self.ec2_client.get_paginator("describe_capacity_reservations")
            reservation_data = None

            for page in paginator.paginate(CapacityReservationIds=[request.resourceId]):
                if len(page["CapacityReservations"]) > 0:
                    reservation_data = page["CapacityReservations"][0]
                    break

            if not reservation_data:
                raise ValueError(f"Capacity Reservation {request.resourceId} not found.")

            state = reservation_data["State"]

            if state == "active":
                request.update_status(RequestStatus.RUNNING, "Capacity Reservation is active.")
                return request

            elif state == "cancelled":
                request.update_status(RequestStatus.COMPLETE, "Capacity Reservation has been canceled.")
                return request

            else:
                request.update_status(RequestStatus.COMPLETE_WITH_ERRORS, f"Capacity Reservation is in state {state}.")
                return request

        except boto3.exceptions.Boto3Error as e:
            raise RuntimeError(f"Failed to check Capacity Reservation status: {str(e)}")

    def get_reservation_config(self, request: Request) -> Dict[str, Any]:
        """
        Generate the configuration for creating an On-Demand Capacity Reservation.
        """
        if not (request.numRequested > 0):
            raise ValueError("The number of requested instances must be greater than zero.")

        return {
            "InstanceType": "t3.micro",
            "InstancePlatform": "Linux/UNIX",
            "AvailabilityZone": "us-east-1a",
            "Tenancy": "default",
            "TotalInstanceCount": request.numRequested,
            "EbsOptimized": True,
            "EphemeralStorage": False,
            "EndDateType": "unlimited"
        }

    def validate_request(self, request: Request) -> None:
        """
        Validate a given Capacity Reservation creation or modification request.
        """
        super().validate_request(request)

        if not (request.numRequested > 0):
            raise ValueError("The number of requested instances must be greater than zero.")
