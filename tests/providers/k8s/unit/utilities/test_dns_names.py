"""Unit tests for the canonical DNS-1123 name validators in
:mod:`orb.providers.k8s.utilities.dns_names`.

Covers:

* :data:`DNS_1123_LABEL_REGEX` — character-set and structural rules.
* :data:`DNS_1123_SUBDOMAIN_REGEX` — dot-separated label rules.
* :func:`validate_dns_1123_label` — raises on empty / too long / bad chars.
* :func:`validate_dns_1123_subdomain` — raises on empty / too long / bad chars.

Also verifies the label-vs-subdomain semantic distinction is preserved:
subdomains accept dots; labels do not.
"""

from __future__ import annotations

import pytest

from orb.providers.k8s.utilities.dns_names import (  # noqa: E402
    DNS_1123_LABEL_MAX_LEN,
    DNS_1123_LABEL_REGEX,
    DNS_1123_SUBDOMAIN_MAX_LEN,
    DNS_1123_SUBDOMAIN_REGEX,
    validate_dns_1123_label,
    validate_dns_1123_subdomain,
)

# ---------------------------------------------------------------------------
# DNS_1123_LABEL_REGEX — pattern match / no-match
# ---------------------------------------------------------------------------


class TestDns1123LabelRegex:
    def test_single_char_lowercase(self) -> None:
        assert DNS_1123_LABEL_REGEX.match("a")

    def test_alphanumeric_only(self) -> None:
        assert DNS_1123_LABEL_REGEX.match("abc123")

    def test_hyphen_in_middle(self) -> None:
        assert DNS_1123_LABEL_REGEX.match("my-namespace")

    def test_digits_only(self) -> None:
        assert DNS_1123_LABEL_REGEX.match("123")

    def test_rejects_uppercase(self) -> None:
        assert not DNS_1123_LABEL_REGEX.match("MyNamespace")

    def test_rejects_leading_hyphen(self) -> None:
        assert not DNS_1123_LABEL_REGEX.match("-orb")

    def test_rejects_trailing_hyphen(self) -> None:
        assert not DNS_1123_LABEL_REGEX.match("orb-")

    def test_rejects_underscore(self) -> None:
        assert not DNS_1123_LABEL_REGEX.match("orb_system")

    def test_rejects_dot(self) -> None:
        # Labels do NOT allow dots — that is the subdomain form.
        assert not DNS_1123_LABEL_REGEX.match("orb.io")

    def test_rejects_empty_string(self) -> None:
        assert not DNS_1123_LABEL_REGEX.match("")


# ---------------------------------------------------------------------------
# DNS_1123_SUBDOMAIN_REGEX — pattern match / no-match
# ---------------------------------------------------------------------------


class TestDns1123SubdomainRegex:
    def test_single_label(self) -> None:
        assert DNS_1123_SUBDOMAIN_REGEX.match("orb")

    def test_dotted_two_labels(self) -> None:
        assert DNS_1123_SUBDOMAIN_REGEX.match("orb.io")

    def test_dotted_three_labels(self) -> None:
        assert DNS_1123_SUBDOMAIN_REGEX.match("my-company.example.com")

    def test_rejects_uppercase(self) -> None:
        assert not DNS_1123_SUBDOMAIN_REGEX.match("ORB.io")

    def test_rejects_leading_hyphen_on_segment(self) -> None:
        assert not DNS_1123_SUBDOMAIN_REGEX.match("-orb.io")

    def test_rejects_trailing_hyphen_on_segment(self) -> None:
        assert not DNS_1123_SUBDOMAIN_REGEX.match("orb-.io")

    def test_rejects_double_dot(self) -> None:
        assert not DNS_1123_SUBDOMAIN_REGEX.match("orb..io")

    def test_rejects_leading_dot(self) -> None:
        assert not DNS_1123_SUBDOMAIN_REGEX.match(".orb.io")

    def test_rejects_trailing_dot(self) -> None:
        assert not DNS_1123_SUBDOMAIN_REGEX.match("orb.io.")

    def test_rejects_empty_string(self) -> None:
        assert not DNS_1123_SUBDOMAIN_REGEX.match("")


# ---------------------------------------------------------------------------
# validate_dns_1123_label — happy paths
# ---------------------------------------------------------------------------


class TestValidateDns1123Label:
    def test_valid_simple_label(self) -> None:
        validate_dns_1123_label("default")  # must not raise

    def test_valid_label_with_hyphens(self) -> None:
        validate_dns_1123_label("orb-system")

    def test_valid_max_length_label(self) -> None:
        validate_dns_1123_label("a" * DNS_1123_LABEL_MAX_LEN)

    def test_valid_single_char(self) -> None:
        validate_dns_1123_label("z")

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_raises_on_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            validate_dns_1123_label("")

    def test_raises_on_too_long(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            validate_dns_1123_label("a" * (DNS_1123_LABEL_MAX_LEN + 1))

    def test_raises_on_uppercase(self) -> None:
        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            validate_dns_1123_label("OrbSystem")

    def test_raises_on_leading_hyphen(self) -> None:
        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            validate_dns_1123_label("-orb")

    def test_raises_on_trailing_hyphen(self) -> None:
        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            validate_dns_1123_label("orb-")

    def test_raises_on_dot(self) -> None:
        # Dots are subdomain-only; labels reject them.
        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            validate_dns_1123_label("orb.io")

    def test_field_name_appears_in_error(self) -> None:
        with pytest.raises(ValueError, match="my_field"):
            validate_dns_1123_label("BAD", field_name="my_field")


# ---------------------------------------------------------------------------
# validate_dns_1123_subdomain — happy paths
# ---------------------------------------------------------------------------


class TestValidateDns1123Subdomain:
    def test_valid_simple(self) -> None:
        validate_dns_1123_subdomain("orb")

    def test_valid_dotted(self) -> None:
        validate_dns_1123_subdomain("orb.io")

    def test_valid_max_length(self) -> None:
        # "a." * 126 + "a" = 252 + 1 = 253 chars, exactly at the limit.
        sub253 = "a." * 126 + "a"
        assert len(sub253) == 253
        validate_dns_1123_subdomain(sub253)

    def test_valid_single_char(self) -> None:
        validate_dns_1123_subdomain("a")

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_raises_on_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            validate_dns_1123_subdomain("")

    def test_raises_on_too_long(self) -> None:
        # 254 chars: "a." * 127 = 254.
        sub254 = "a." * 127
        assert len(sub254) == 254
        with pytest.raises(ValueError, match="exceeds"):
            validate_dns_1123_subdomain(sub254)

    def test_raises_on_uppercase(self) -> None:
        with pytest.raises(ValueError, match="not a valid DNS-1123 subdomain"):
            validate_dns_1123_subdomain("ORB.io")

    def test_raises_on_leading_dot(self) -> None:
        with pytest.raises(ValueError, match="not a valid DNS-1123 subdomain"):
            validate_dns_1123_subdomain(".orb.io")

    def test_field_name_appears_in_error(self) -> None:
        with pytest.raises(ValueError, match="label_prefix"):
            validate_dns_1123_subdomain("BAD!", field_name="label_prefix")


# ---------------------------------------------------------------------------
# Label vs subdomain semantic distinction
# ---------------------------------------------------------------------------


class TestLabelVsSubdomainDistinction:
    """Dots are the key distinguishing feature: subdomains accept them,
    labels reject them.  Both forms reject uppercase, leading/trailing hyphens,
    and underscores — confirming shared RFC-1123 foundations.
    """

    def test_dotted_value_accepted_by_subdomain_not_label(self) -> None:
        validate_dns_1123_subdomain("orb.io")  # must pass
        with pytest.raises(ValueError):
            validate_dns_1123_label("orb.io")  # must fail

    def test_hyphenated_value_accepted_by_both(self) -> None:
        validate_dns_1123_label("orb-system")
        validate_dns_1123_subdomain("orb-system")

    def test_underscore_rejected_by_both(self) -> None:
        with pytest.raises(ValueError):
            validate_dns_1123_label("orb_system")
        with pytest.raises(ValueError):
            validate_dns_1123_subdomain("orb_system")

    def test_label_max_63_subdomain_max_253(self) -> None:
        assert DNS_1123_LABEL_MAX_LEN == 63
        assert DNS_1123_SUBDOMAIN_MAX_LEN == 253
