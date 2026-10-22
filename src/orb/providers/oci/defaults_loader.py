"""OCI defaults without creating a configured provider instance."""

import json
from importlib.resources import files


class OCIDefaultsLoader:
    def load_defaults(self) -> dict:
        data = json.loads(
            files("orb.providers.oci.config")
            .joinpath("oci_defaults.json")
            .read_text(encoding="utf-8")
        )
        return {"provider": {"provider_defaults": data["provider"]["provider_defaults"]}}
