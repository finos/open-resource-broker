"""Opaque keyset cursors: round trip, legacy offsets, rejection, fingerprints."""

from __future__ import annotations

import base64
import json

import pytest

from orb.application.services.orchestration.dtos import decode_cursor, encode_cursor
from orb.application.services.orchestration.page_cursor import (
    DecodedCursor,
    KeysetPaginated,
    decode_page_cursor,
    encode_page_cursor,
    next_cursor_for,
    query_fingerprint,
)
from orb.domain.base.exceptions import ValidationError

FP = query_fingerprint("machines", status="running", sort="-created_at")


def _raw(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.unit
class TestRoundTrip:
    def test_position_and_offset_survive(self) -> None:
        cursor = encode_page_cursor(FP, "-created_at", ("2026-01-01", "m-7"), 50)

        assert decode_page_cursor(cursor, FP, "-created_at") == DecodedCursor(
            position=("2026-01-01", "m-7"), offset=50
        )

    def test_no_cursor_is_none(self) -> None:
        assert decode_page_cursor(None, FP, "-created_at") is None
        assert decode_page_cursor("", FP, "-created_at") is None

    def test_empty_sort_text_is_a_valid_position(self) -> None:
        cursor = encode_page_cursor(FP, "x", ("", "m-1"), 1)

        assert decode_page_cursor(cursor, FP, "x").position == ("", "m-1")  # type: ignore[union-attr]

    def test_cursor_carries_the_cumulative_offset_key_for_older_releases(self) -> None:
        cursor = encode_page_cursor(FP, "-created_at", ("k", "i"), 150)

        assert decode_cursor(cursor) == 150  # the legacy decoder pages by offset

    def test_padding_is_optional(self) -> None:
        cursor = encode_page_cursor(FP, "s", ("k", "i"), 1).rstrip("=")

        assert decode_page_cursor(cursor, FP, "s") is not None


@pytest.mark.unit
class TestLegacyCursors:
    def test_offset_cursor_is_accepted(self) -> None:
        assert decode_page_cursor(encode_cursor(40), FP, "s") == DecodedCursor(None, 40)

    @pytest.mark.parametrize("offset", [-1, "7", None, True, 1.5])
    def test_bad_legacy_offsets_are_rejected(self, offset: object) -> None:
        with pytest.raises(ValidationError):
            decode_page_cursor(_raw({"offset": offset}), FP, "s")

    def test_templates_keep_their_forgiving_decoder(self) -> None:
        # Templates page by offset with the legacy helpers, which stay as they were
        assert decode_cursor("not a cursor") == 0
        assert decode_cursor(None) == 0
        assert encode_cursor(10) == _raw({"offset": 10}).replace(" ", "")


@pytest.mark.unit
class TestRejection:
    @pytest.mark.parametrize(
        "cursor",
        ["!!!", "bm90IGpzb24=", _raw([1, 2]), _raw("x"), _raw({}), _raw({"k": ["a", "b"]})],
        ids=["not-base64", "not-json", "list", "string", "empty-object", "no-version"],
    )
    def test_garbage_is_an_error_not_page_one(self, cursor: str) -> None:
        with pytest.raises(ValidationError):
            decode_page_cursor(cursor, FP, "s")

    @pytest.mark.parametrize(
        "mutation",
        [
            {"v": 3},
            {"k": ["only-one"]},
            {"k": [1, 2]},
            {"offset": -5},
            {"offset": "x"},
        ],
    )
    def test_malformed_v2_payloads_are_rejected(self, mutation: dict) -> None:
        payload = {"v": 2, "s": "s", "f": FP, "k": ["a", "b"], "offset": 1, **mutation}

        with pytest.raises(ValidationError):
            decode_page_cursor(_raw(payload), FP, "s")

    def test_a_cursor_for_another_sort_is_rejected(self) -> None:
        cursor = encode_page_cursor(FP, "-created_at", ("a", "b"), 1)

        with pytest.raises(ValidationError):
            decode_page_cursor(cursor, FP, "status")

    def test_a_cursor_for_another_query_is_rejected(self) -> None:
        cursor = encode_page_cursor(FP, "s", ("a", "b"), 1)
        other = query_fingerprint("machines", status="pending", sort="s")

        with pytest.raises(ValidationError):
            decode_page_cursor(cursor, other, "s")

    def test_the_error_never_repeats_the_cursor(self) -> None:
        cursor = _raw({"v": 2, "s": "s", "f": "forged", "k": ["secret-value", "id"]})

        with pytest.raises(ValidationError) as exc_info:
            decode_page_cursor(cursor, FP, "s")

        assert "secret-value" not in str(exc_info.value)
        assert cursor not in str(exc_info.value)


@pytest.mark.unit
class TestFingerprint:
    BASE = {
        "status": "running",
        "provider_name": "aws-a",
        "provider_type": "aws",
        "request_id": "req-1",
        "template_id": "t-1",
        "q": "web",
        "filter_expressions": ["a=b", "c=d"],
        "sync": True,
        "sort": "-created_at",
    }

    def test_every_result_affecting_input_changes_the_fingerprint(self) -> None:
        base = query_fingerprint("machines", **self.BASE)
        changed = {
            "status": "pending",
            "provider_name": "aws-b",
            "provider_type": "k8s",
            "request_id": "req-2",
            "template_id": "t-2",
            "q": "db",
            "filter_expressions": ["a=b"],
            "sync": False,
            "sort": "status",
        }

        for name, value in changed.items():
            assert query_fingerprint("machines", **{**self.BASE, name: value}) != base, name

    def test_the_kind_changes_it(self) -> None:
        assert query_fingerprint("machines", **self.BASE) != query_fingerprint(
            "requests", **self.BASE
        )

    def test_expression_order_does_not_matter(self) -> None:
        a = query_fingerprint("m", filter_expressions=["x=1", "y=2"])
        b = query_fingerprint("m", filter_expressions=["y=2", "x=1"])

        assert a == b

    def test_unset_and_empty_inputs_hash_alike(self) -> None:
        assert query_fingerprint("m", status=None, q="", filter_expressions=[]) == (
            query_fingerprint("m")
        )


@pytest.mark.unit
class TestNextCursor:
    def test_keyset_result_with_more_rows_yields_a_cursor_with_cumulative_offset(self) -> None:
        result = KeysetPaginated(items=[1, 2], total_count=9, next_position=("k", "i"))

        handled, cursor = next_cursor_for(result, fingerprint=FP, sort="s", offset=10, returned=2)

        assert handled is True
        assert decode_page_cursor(cursor, FP, "s") == DecodedCursor(("k", "i"), 12)

    def test_last_page_has_no_cursor(self) -> None:
        result = KeysetPaginated(items=[1], total_count=1, next_position=None)

        assert next_cursor_for(result, fingerprint=FP, sort="s", offset=0, returned=1) == (
            True,
            None,
        )

    def test_other_results_are_left_to_the_offset_cursor(self) -> None:
        assert next_cursor_for([1, 2], fingerprint=FP, sort="s", offset=0, returned=2) == (
            False,
            None,
        )
