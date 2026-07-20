"""Structured output contract for every answer (machine-checkable for eval)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Citation(BaseModel):
    source_file: str = Field(description="Originating source file for the cited data.")
    locator: str = Field(description="Where in the source (sheet!range, page, row id).")
    snippet: str | None = Field(
        default=None, description="Short supporting snippet or value, if useful."
    )


class AgentAnswer(BaseModel):
    answer: str = Field(description="The natural-language answer for the user.")
    confidence: float = Field(
        description="Overall confidence in the answer, 0.0-1.0.", ge=0.0, le=1.0
    )
    abstained: bool = Field(
        default=False,
        description="True when the agent declined to answer (could not ground it).",
    )
    reason: str | None = Field(
        default=None,
        description="Why the agent abstained or what clarification is needed.",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Sources backing the answer (required for any quantitative claim).",
    )
    sql_used: str | None = Field(
        default=None, description="The SQL query executed to produce the answer."
    )
