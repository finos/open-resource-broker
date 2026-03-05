"""Paginated response DTOs for list endpoints."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from application.dto.base import PaginationMetadata  # type: ignore[attr-defined]

T = TypeVar("T")


class PaginatedListResponse(BaseModel, Generic[T]):
    """Generic paginated list response with metadata."""

    model_config = ConfigDict(frozen=True)

    data: list[T]
    pagination: PaginationMetadata


class PaginatedRequestsResponse(BaseModel):
    """Paginated response for request lists."""

    model_config = ConfigDict(frozen=True)

    data: list
    pagination: PaginationMetadata


class PaginatedMachinesResponse(BaseModel):
    """Paginated response for machine lists."""

    model_config = ConfigDict(frozen=True)

    data: list
    pagination: PaginationMetadata


class PaginatedTemplatesResponse(BaseModel):
    """Paginated response for template lists."""

    model_config = ConfigDict(frozen=True)

    data: list
    pagination: PaginationMetadata
