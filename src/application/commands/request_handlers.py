"""Command handlers for request operations.

This module re-exports all request handlers for backward compatibility.
Handlers are organized into focused modules:
- request_creation_handlers: CreateMachineRequestHandler, CreateReturnRequestHandler
- request_lifecycle_handlers: UpdateRequestStatusHandler, CancelRequestHandler, CompleteRequestHandler
- request_sync_handlers: PopulateMachineIdsHandler, SyncRequestHandler
"""

from application.commands.request_creation_handlers import (
    CreateMachineRequestHandler,
    CreateReturnRequestHandler,
)
from application.commands.request_lifecycle_handlers import (
    CancelRequestHandler,
    CompleteRequestHandler,
    UpdateRequestStatusHandler,
)
from application.commands.request_sync_handlers import (
    PopulateMachineIdsHandler,
    SyncRequestHandler,
)

__all__ = [
    "CreateMachineRequestHandler",
    "CreateReturnRequestHandler",
    "UpdateRequestStatusHandler",
    "CancelRequestHandler",
    "CompleteRequestHandler",
    "PopulateMachineIdsHandler",
    "SyncRequestHandler",
]
