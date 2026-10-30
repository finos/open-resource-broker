"""Fuzz template and AWS identifier validators.

Targets:
  * Template provider_name / provider_type validation
  * AWSInstanceType and AWSARN value objects

Properties:
  * Only ValueError (pydantic ValidationError is a subclass) escapes.
  * Any accepted value fully matches the documented pattern. In particular a
    trailing newline must never be accepted.
"""

from __future__ import annotations

import re
import sys

import _common
import atheris

_common.prepare_environment()

with atheris.instrument_imports(include=["orb"]):
    from orb.domain.template.template_aggregate import Template
    from orb.providers.aws.domain.template.value_objects import AWSARN, AWSInstanceType

_PROVIDER_NAME = re.compile(r"[a-zA-Z0-9_-]+")
_PROVIDER_TYPE = re.compile(r"[a-z0-9]+")
_INSTANCE_TYPE = re.compile(r"[a-z][0-9]+[a-z]*\.[a-z0-9]+")
_ARN = re.compile(r"arn:aws:[a-zA-Z0-9\-]+:[a-zA-Z0-9\-]*:[0-9]{12}:.+")


def _text(fdp: atheris.FuzzedDataProvider) -> str:
    return fdp.ConsumeBytes(fdp.remaining_bytes()).decode("utf-8", errors="replace")


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    op = fdp.ConsumeIntInRange(0, 2)

    if op == 0:
        name, _, ptype = _text(fdp).partition("\0")
        try:
            template = Template(
                template_id="fuzz",
                provider_name=name or None,
                provider_type=ptype or None,
            )
        except ValueError:
            return
        if template.provider_name:
            assert _PROVIDER_NAME.fullmatch(template.provider_name), repr(template.provider_name)
        if template.provider_type:
            assert _PROVIDER_TYPE.fullmatch(template.provider_type), repr(template.provider_type)

    elif op == 1:
        text = _text(fdp)
        try:
            value = AWSInstanceType(value=text).value
        except ValueError:
            return
        assert _INSTANCE_TYPE.fullmatch(value), repr(value)

    else:
        text = _text(fdp)
        try:
            value = AWSARN(value=text).value
        except ValueError:
            return
        assert _ARN.fullmatch(value), repr(value)


if __name__ == "__main__":
    _common.run(atheris, test_one_input)
    sys.exit(0)
