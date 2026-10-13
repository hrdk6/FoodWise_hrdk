"""Shared API shapes."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


class Ok(BaseModel):
    ok: bool = True
    message: str | None = None


class JobRef(BaseModel):
    job_id: str
    status: str = "queued"
