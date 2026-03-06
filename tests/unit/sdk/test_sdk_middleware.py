"""Unit tests for SDK middleware system."""

import pytest

from orb.sdk.middleware import SDKMiddleware, build_middleware_chain


class RecordingMiddleware(SDKMiddleware):
    """Middleware that records calls for testing."""

    def __init__(self, name: str = "recorder"):
        self.name = name
        self.calls: list[dict] = []

    async def process(self, method_name, args, kwargs, next_handler):
        self.calls.append({"method": method_name, "kwargs": dict(kwargs)})
        result = await next_handler(args, kwargs)
        self.calls[-1]["result"] = result
        return result


class TransformMiddleware(SDKMiddleware):
    """Middleware that transforms kwargs and results."""

    async def process(self, method_name, args, kwargs, next_handler):
        kwargs["injected"] = True
        result = await next_handler(args, kwargs)
        if isinstance(result, dict):
            result["transformed"] = True
        return result


class ShortCircuitMiddleware(SDKMiddleware):
    """Middleware that short-circuits without calling next."""

    async def process(self, method_name, args, kwargs, next_handler):
        return {"short_circuited": True}


class ErrorMiddleware(SDKMiddleware):
    """Middleware that raises an error."""

    async def process(self, method_name, args, kwargs, next_handler):
        raise ValueError("middleware error")


# ---------------------------------------------------------------------------
# SDKMiddleware base class
# ---------------------------------------------------------------------------


class TestSDKMiddlewareBase:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            SDKMiddleware()


# ---------------------------------------------------------------------------
# build_middleware_chain
# ---------------------------------------------------------------------------


class TestBuildMiddlewareChain:
    @pytest.mark.asyncio
    async def test_no_middleware_calls_method_directly(self):
        async def fake_method(**kwargs):
            return {"result": "ok", **kwargs}

        chain = build_middleware_chain([], "test_method", fake_method)
        result = await chain(key="value")
        assert result == {"result": "ok", "key": "value"}

    @pytest.mark.asyncio
    async def test_single_middleware(self):
        recorder = RecordingMiddleware()

        async def fake_method(**kwargs):
            return {"data": 42}

        chain = build_middleware_chain([recorder], "list_templates", fake_method)
        result = await chain(active_only=True)

        assert result == {"data": 42}
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["method"] == "list_templates"
        assert recorder.calls[0]["kwargs"]["active_only"] is True

    @pytest.mark.asyncio
    async def test_middleware_chain_order(self):
        """First middleware added is outermost (called first)."""
        order = []

        class OrderMiddleware(SDKMiddleware):
            def __init__(self, label):
                self.label = label

            async def process(self, method_name, args, kwargs, next_handler):
                order.append(f"{self.label}_before")
                result = await next_handler(args, kwargs)
                order.append(f"{self.label}_after")
                return result

        async def fake_method(**kwargs):
            order.append("method")
            return "done"

        mw1 = OrderMiddleware("first")
        mw2 = OrderMiddleware("second")
        chain = build_middleware_chain([mw1, mw2], "test", fake_method)
        await chain()

        assert order == ["first_before", "second_before", "method", "second_after", "first_after"]

    @pytest.mark.asyncio
    async def test_middleware_transforms_kwargs_and_result(self):
        async def fake_method(**kwargs):
            return {"received_injected": kwargs.get("injected", False)}

        chain = build_middleware_chain([TransformMiddleware()], "test", fake_method)
        result = await chain(key="val")

        assert result["received_injected"] is True
        assert result["transformed"] is True

    @pytest.mark.asyncio
    async def test_middleware_short_circuit(self):
        async def fake_method(**kwargs):
            raise AssertionError("should not be called")

        chain = build_middleware_chain([ShortCircuitMiddleware()], "test", fake_method)
        result = await chain()

        assert result == {"short_circuited": True}

    @pytest.mark.asyncio
    async def test_middleware_error_propagates(self):
        async def fake_method(**kwargs):
            return "ok"

        chain = build_middleware_chain([ErrorMiddleware()], "test", fake_method)
        with pytest.raises(ValueError, match="middleware error"):
            await chain()


# ---------------------------------------------------------------------------
# ORBClient.add_middleware integration
# ---------------------------------------------------------------------------


class TestORBClientMiddleware:
    def test_add_middleware_before_init(self):
        from orb.sdk.client import ORBClient

        sdk = ORBClient(provider="mock")
        recorder = RecordingMiddleware()
        sdk.add_middleware(recorder)

        assert len(sdk._middlewares) == 1
        assert sdk._middlewares[0] is recorder

    def test_add_middleware_not_initialized_no_error(self):
        from orb.sdk.client import ORBClient

        sdk = ORBClient(provider="mock")
        sdk.add_middleware(RecordingMiddleware())
        # Should not raise — middleware stored for later application
