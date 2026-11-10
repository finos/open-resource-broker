"""Wire models for the HostFactory response format.

The REST API answers in the configured scheduler's format. These models describe
the HostFactory shape (IBM Spectrum Symphony Host Factory 7.3.2, camelCase) so
SDK generators can type it. They are documentation only: routes return what the
scheduler formats and are not validated through them. Unknown keys are allowed
because ORB adds documented extensions next to the IBM fields.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class HfMachine(BaseModel):
    """A machine entry of a HostFactory request status."""

    model_config = ConfigDict(extra="allow")

    machineId: str
    name: str
    result: Literal["executing", "fail", "succeed"]
    # IBM lists running, stopped, terminated, shutting-down and stopping. ORB also
    # sends its own lifecycle values (for example pending and launching) while a
    # machine is not yet running, so the type stays an open string.
    status: Optional[str] = None
    privateIpAddress: str
    publicIpAddress: Optional[str] = None
    # Epoch seconds; int64 so generated clients do not overflow in 2038.
    launchtime: int = Field(json_schema_extra={"format": "int64"})
    message: str = ""


class HfRequestStatusItem(BaseModel):
    """One request in a HostFactory request status response."""

    model_config = ConfigDict(extra="allow")

    requestId: str
    status: Literal["running", "complete", "complete_with_error"]
    message: str = ""
    machines: list[HfMachine] = []


class HfRequestStatusResponse(BaseModel):
    """HostFactory getRequestStatus response."""

    model_config = ConfigDict(extra="allow")

    requests: list[HfRequestStatusItem]


HF_MODELS: tuple[type[BaseModel], ...] = (HfMachine, HfRequestStatusItem, HfRequestStatusResponse)

# Operation id -> HostFactory response model, for the per-operation format map.
HF_OPERATION_MODELS: dict[str, type[BaseModel]] = {
    "getRequestStatus": HfRequestStatusResponse,
}
