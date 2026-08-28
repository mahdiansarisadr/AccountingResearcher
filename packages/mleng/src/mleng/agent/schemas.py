"""Structured output contract for every answer."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentAnswer(BaseModel):
    answer: str = Field(description="The natural-language answer for the user.")
    confidence: float = Field(
        description="Overall confidence in the answer, 0.0-1.0.", ge=0.0, le=1.0
    )
    abstained: bool = Field(
        default=False,
        description="True when the agent declined to answer or recommend.",
    )
    reason: str | None = Field(
        default=None,
        description="Why the agent abstained or what clarification is needed.",
    )
