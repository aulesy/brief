"""Brief HTTP API — FastAPI endpoint for agent integration.

Usage:
    uvicorn brief.api:app --port 8080
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Brief Service", version="0.1.0")


class BriefRequest(BaseModel):
    uri: str
    query: str = "summarize this content"
    depth: int = 1
    force: bool = False


class BriefResponse(BaseModel):
    rendered: str
    cached: bool = False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/brief", response_model=BriefResponse)
def create_brief(req: BriefRequest):
    from .store import BriefStore
    from .service import brief

    store = BriefStore()
    was_cached = store.check_query(req.uri, req.query, req.depth) is not None

    rendered = brief(req.uri, req.query, force=req.force, depth=req.depth)
    return BriefResponse(rendered=rendered, cached=was_cached and not req.force)


@app.get("/briefs")
def list_briefs():
    from .store import BriefStore

    store = BriefStore()
    return store.list_all()
