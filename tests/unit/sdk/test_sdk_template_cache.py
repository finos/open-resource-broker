"""Opt-in, time-limited cache for ORBClient.list_templates."""

import asyncio
import copy
import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.sdk.client import ORBClient
from orb.sdk.config import SDKConfig
from orb.sdk.exceptions import ConfigurationError, SDKError

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr("orb.sdk.client.time.monotonic", fake)
    return fake


def _client(ttl: float = 60, **config) -> ORBClient:
    sdk = ORBClient(config={"provider_type": "aws", **config}, template_cache_ttl_seconds=ttl)
    sdk._initialized = True
    sdk._container = MagicMock()
    sdk._dispatch_catalog = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda key, dto, format=None: {"templates": [{"template_id": "t1"}]}
    )
    return sdk


def _calls(sdk: ORBClient, key: str = "list_templates") -> int:
    return sum(1 for c in sdk._dispatch_catalog.await_args_list if c.args[0] == key)  # type: ignore[attr-defined]


class TestListTemplatesCache:
    @pytest.mark.asyncio
    async def test_disabled_by_default_loads_every_time(self):
        sdk = _client(ttl=0)
        await sdk.list_templates()
        await sdk.list_templates()
        assert _calls(sdk) == 2

    @pytest.mark.asyncio
    async def test_hit_within_lifetime(self, clock):
        sdk = _client()
        first = await sdk.list_templates()
        clock.now += 59
        second = await sdk.list_templates()
        assert _calls(sdk) == 1
        assert second == first

    @pytest.mark.asyncio
    async def test_different_arguments_do_not_share_entries(self, clock):
        sdk = _client()
        await sdk.list_templates()
        await sdk.list_templates(active_only=False)
        await sdk.list_templates(format="json")
        await sdk.list_templates(limit=5)
        assert _calls(sdk) == 4

    @pytest.mark.asyncio
    async def test_expiry_reloads(self, clock):
        sdk = _client()
        await sdk.list_templates()
        clock.now += 60
        await sdk.list_templates()
        assert _calls(sdk) == 2

    @pytest.mark.asyncio
    async def test_clear_cache_forces_reload(self, clock):
        sdk = _client()
        await sdk.list_templates()
        sdk.clear_cache()
        await sdk.list_templates()
        assert _calls(sdk) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mutation,args",
        [
            ("create_template", {"template_id": "n", "provider_api": "EC2Fleet", "image_id": "i"}),
            ("update_template", {"template_id": "t1"}),
            ("delete_template", {"template_id": "t1"}),
            ("refresh_templates", {}),
        ],
    )
    async def test_mutations_invalidate(self, clock, mutation, args):
        sdk = _client()
        await sdk.list_templates()
        await getattr(sdk, mutation)(**args)
        await sdk.list_templates()
        assert _calls(sdk) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mutation,args",
        [
            ("create_template", {"template_id": "n", "provider_api": "EC2Fleet", "image_id": "i"}),
            ("update_template", {"template_id": "t1"}),
            ("refresh_templates", {}),
        ],
    )
    async def test_failed_mutations_still_invalidate(self, clock, mutation, args):
        sdk = _client()
        await sdk.list_templates()
        sdk._dispatch_catalog.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError):
            await getattr(sdk, mutation)(**args)
        sdk._dispatch_catalog.side_effect = lambda key, dto, format=None: {"templates": []}  # type: ignore[attr-defined]
        await sdk.list_templates()
        assert _calls(sdk) == 2

    @pytest.mark.asyncio
    async def test_caller_mutation_does_not_alter_cache(self, clock):
        sdk = _client()
        first = await sdk.list_templates()
        first["templates"].append("junk")
        second = await sdk.list_templates()
        second["templates"].clear()
        third = await sdk.list_templates()
        assert third == {"templates": [{"template_id": "t1"}]}

    @pytest.mark.asyncio
    async def test_failed_load_is_not_cached(self, clock):
        sdk = _client()
        sdk._dispatch_catalog.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError):
            await sdk.list_templates()
        sdk._dispatch_catalog.side_effect = lambda key, dto, format=None: {"templates": []}  # type: ignore[attr-defined]
        await sdk.list_templates()
        assert _calls(sdk) == 2

    @pytest.mark.asyncio
    async def test_uninitialised_client_still_raises(self, clock):
        sdk = _client()
        sdk._initialized = False
        with pytest.raises(SDKError):
            await sdk.list_templates()

    @pytest.mark.asyncio
    async def test_load_overlapping_a_mutation_is_not_stored(self, clock):
        sdk = _client()
        gate = asyncio.Event()
        stale = {"templates": [{"template_id": "old"}]}

        async def dispatch(key, dto, format=None):
            if key == "list_templates" and not gate.is_set():
                await gate.wait()
                return stale
            return {"templates": [{"template_id": "new"}]}

        sdk._dispatch_catalog.side_effect = dispatch  # type: ignore[attr-defined]
        pending = asyncio.ensure_future(sdk.list_templates())
        await asyncio.sleep(0)
        await sdk.update_template(template_id="t1")
        gate.set()
        assert await pending == stale
        assert (await sdk.list_templates())["templates"][0]["template_id"] == "new"

    @pytest.mark.asyncio
    async def test_disabled_cache_stores_and_copies_nothing(self, monkeypatch):
        sdk = _client(ttl=0)

        def boom(*_a, **_k):
            raise AssertionError("deepcopy must not run when the cache is off")

        monkeypatch.setattr(copy, "deepcopy", boom)
        await sdk.list_templates()
        assert not sdk._template_cache

    @pytest.mark.asyncio
    async def test_omitted_provider_falls_back_to_config_and_keys_on_it(self, clock):
        sdk = _client()
        await sdk.list_templates()
        sdk._config.provider_name = "other"
        await sdk.list_templates()
        assert _calls(sdk) == 2
        await sdk.list_templates(provider_name="other")
        assert _calls(sdk) == 2

    @pytest.mark.asyncio
    async def test_cleanup_clears_cache(self, clock):
        sdk = _client()
        await sdk.list_templates()
        await sdk.cleanup()
        assert not sdk._template_cache

    def test_clear_cache_on_instance_built_without_init(self):
        sdk = ORBClient.__new__(ORBClient)
        sdk.clear_cache()
        assert sdk._template_cache_gen == 1

    def test_list_templates_is_not_middleware_wrapped(self):
        assert "list_templates" in ORBClient._EXPLICIT_METHODS


class TestTemplateCacheConfig:
    def test_default_is_off(self):
        assert SDKConfig().template_cache_ttl_seconds == 0

    def test_from_env_parses_float(self, monkeypatch):
        monkeypatch.setenv("ORB_TEMPLATE_CACHE_TTL_SECONDS", "1.5")
        assert SDKConfig.from_env().template_cache_ttl_seconds == 1.5

    def test_from_env_rejects_bad_value(self, monkeypatch):
        monkeypatch.setenv("ORB_TEMPLATE_CACHE_TTL_SECONDS", "soon")
        with pytest.raises(ConfigurationError):
            SDKConfig.from_env()

    def test_from_dict_and_round_trip(self):
        config = SDKConfig.from_dict({"template_cache_ttl_seconds": 30})
        assert config.template_cache_ttl_seconds == 30
        assert config.to_dict()["template_cache_ttl_seconds"] == 30
        assert "template_cache_ttl_seconds" not in config.custom_config

    @pytest.mark.parametrize("value", [-1, math.nan, math.inf, True, "60", None])
    def test_validate_rejects(self, value):
        with pytest.raises(ConfigurationError):
            SDKConfig(template_cache_ttl_seconds=value).validate()  # type: ignore[arg-type]

    def test_constructor_sets_field_not_custom_config(self):
        sdk = ORBClient(config={"provider_type": "aws"}, template_cache_ttl_seconds=60)
        assert sdk._config.template_cache_ttl_seconds == 60
        assert "template_cache_ttl_seconds" not in sdk._config.custom_config

    def test_constructor_rejects_negative(self):
        with pytest.raises(ConfigurationError):
            ORBClient(config={"provider_type": "aws"}, template_cache_ttl_seconds=-5)

    def test_protocol_has_clear_cache(self):
        from orb.sdk.protocols import ORBClientProtocol

        assert hasattr(ORBClientProtocol, "clear_cache")
