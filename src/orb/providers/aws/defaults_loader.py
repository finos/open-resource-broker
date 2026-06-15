"""AWS provider defaults loader."""

from __future__ import annotations

import json

from orb.domain.base.ports.provider_defaults_loader_port import ProviderDefaultsLoaderPort


class AWSDefaultsLoader:
    """Loads defaults from the bundled ``aws_defaults.json`` config file.

    Satisfies :class:`~orb.domain.base.ports.provider_defaults_loader_port.ProviderDefaultsLoaderPort`.
    """

    def load_defaults(self) -> dict:
        """Return AWS provider defaults from the bundled ``aws_defaults.json``.

        Returns:
            Raw configuration dictionary contributed by the AWS provider.
            Returns an empty dict if the file cannot be read.
        """
        try:
            from importlib.resources import files

            text = (
                files("orb.providers.aws.config")
                .joinpath("aws_defaults.json")
                .read_text(encoding="utf-8")
            )
            return json.loads(text)
        except Exception:
            return {}


# Runtime check that AWSDefaultsLoader satisfies the protocol
assert isinstance(AWSDefaultsLoader(), ProviderDefaultsLoaderPort)
