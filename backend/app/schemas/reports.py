"""Formal Report IR — the only input accepted by HTML/Markdown renderers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReportSection(BaseModel):
    id: str
    title: str
    content: str
    evidence_ids: list[str] = Field(default_factory=list)


class ReportCitation(BaseModel):
    conclusion: str
    evidence_ids: list[str] = Field(default_factory=list)


class ReportIR(BaseModel):
    schema_version: str = "1.0.0"
    title: str
    executive_summary: str
    sections: list[ReportSection] = Field(default_factory=list)
    citation_links: list[ReportCitation] = Field(default_factory=list)
    disclaimer: str = ""
    is_demo: bool = False
    propagation_ref: dict = Field(default_factory=dict)
    fact_check_summary: dict = Field(default_factory=dict)
