"""M4: Provenance 一跳上下游 API 契约（不用图数据库）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProvenanceRef(BaseModel):
    type: str
    id: str
    relation: str | None = None
    label: str | None = None


class ProvenanceResponse(BaseModel):
    root: ProvenanceRef
    upstream: list[ProvenanceRef] = Field(default_factory=list)
    downstream: list[ProvenanceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def ref_dict(
    ref_type: str, ref_id: str, relation: str | None = None, label: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": ref_type, "id": ref_id}
    if relation:
        payload["relation"] = relation
    if label:
        payload["label"] = label
    return payload
