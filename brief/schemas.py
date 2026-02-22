"""Brief v2 schema — storage format for extracted content."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class BriefSource(BaseModel):
    type: str
    uri: str
    tokens_original: int = 0
    hash: str = ""


class BriefPointer(BaseModel):
    at: Optional[str] = None  # human-readable timestamp like "1:25" (videos only)
    sec: float = 0.0
    text: str


class Brief(BaseModel):
    v: int = 2
    source: BriefSource
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    pointers: list[BriefPointer] = Field(default_factory=list)
    chunks: list[dict] = Field(default_factory=list)  # full untruncated text for depth=3
    tokens: int = 0
    created: str = ""
