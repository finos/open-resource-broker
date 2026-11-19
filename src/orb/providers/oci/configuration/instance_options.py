"""OCI instance metadata endpoint options."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class OCIInstanceOptions(BaseModel):
    """Optional launch settings; omission preserves OCI's metadata defaults."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    are_legacy_imds_endpoints_disabled: StrictBool | None = Field(
        default=None, alias="areLegacyImdsEndpointsDisabled"
    )
