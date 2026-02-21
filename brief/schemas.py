"""Brief v2 schema — storage format for extracted content."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BriefSource(BaseModel):
    type: str
    uri: str
    tokens_original: int = 0
    hash: str = ""


class BriefPointer(BaseModel):
    at: str  # human-readable timestamp like "1:25"
    sec: float = 0.0
    text: str


class Brief(BaseModel):
    v: int = 2
    source: BriefSource
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    pointers: list[BriefPointer] = Field(default_factory=list)
    tokens: int = 0
    created: str = ""
