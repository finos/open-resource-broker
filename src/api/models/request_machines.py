"""Request machines API models."""

from typing import Optional

from pydantic import Field, field_validator

from src.api.models.base import APIRequest, APIResponse


class MachineTemplate(APIRequest):
    """Machine template request model."""

    template_id: str = Field(
        description="Unique ID that can identify this template in the cloud provider"
    )
    machine_count: int = Field(
        description="Number of hosts of this template to be provisioned",
        gt=0,  # Greater than 0
    )

    @field_validator("machine_count")
    @classmethod
    def validate_machine_count(cls, v: int) -> int:
        """Validate machine count."""
        if v <= 0:
            raise ValueError("Machine count must be greater than 0")
        return v


class RequestMachinesRequest(APIRequest):
    """Request machines request model."""

    template: MachineTemplate = Field(description="Template to use for provisioning machines")


class Machine(APIRequest):
    """Machine model."""

    machine_id: str = Field(description="ID of the machine being retrieved from provider")
    name: str = Field(description="Host name of the machine")
    result: str = Field(description="Status of this request related to this machine")
    status: Optional[str] = Field(default=None, description="Status of machine")
    private_ip_address: str = Field(description="Private IP address of the machine")
    public_ip_address: Optional[str] = Field(
        default=None, description="Public IP address of the machine"
    )
    launch_time: int = Field(description="Launch time of the machine in seconds (UTC format)")
    message: Optional[str] = Field(
        default=None,
        description="Additional message for the request status of this machine",
    )


class RequestMachinesResponse(APIResponse):
    """Request machines response model."""

    request_id: str = Field(description="Unique ID to identify this request in the cloud provider")
    message: str = Field(
        default="Request VM success from provider.",
        description="Any additional message the caller should know",
    )
