"""Unit tests for setup_logging()."""

import logging

import pytest

from orb.config.schemas.logging_schema import LoggingConfig
from orb.infrastructure.logging import logger as logger_module


@pytest.fixture(autouse=True)
def reset_logging():
    logger_module._logging_initialized = False
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    yield
    logger_module._logging_initialized = False
    for h in list(root.handlers):
        root.removeHandler(h)


def test_setup_logging_uses_format_from_config():
    custom_fmt = "%(levelname)s %(message)s"
    config = LoggingConfig(level="DEBUG", format=custom_fmt, file_path=None, console_enabled=True)
    logger_module.setup_logging(config)
    root = logging.getLogger()
    console_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert console_handlers, "expected a console handler"
    assert console_handlers[0].formatter._fmt == custom_fmt


def test_setup_logging_fallback_on_empty_format():
    config = LoggingConfig(level="INFO", format="", file_path=None, console_enabled=True)
    logger_module.setup_logging(config)
    root = logging.getLogger()
    console_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert console_handlers[0].formatter._fmt != ""
