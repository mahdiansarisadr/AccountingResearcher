"""Assemble the Accounting Research Assistant with create_agent."""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langgraph.checkpoint.memory import InMemorySaver

from ..core.settings import get_settings
from .prompts import get_system_prompt
from .schemas import AgentAnswer
from .tools import TOOLS


def _middleware() -> list:
    # Retries use small delays to protect the ~10s latency budget.
    # exit_behavior="end" funnels limit breaches into a graceful stop
    # (which the model surfaces as an abstention) rather than raising.
    return [
        ModelRetryMiddleware(max_retries=2, initial_delay=0.5, max_delay=4.0),
        ToolRetryMiddleware(max_retries=1, initial_delay=0.3, max_delay=2.0),
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=10, exit_behavior="end"),
        # Content control: mask emails that appear in tool results / output.
        PIIMiddleware("email", strategy="mask", apply_to_tool_results=True),
    ]


def build_agent(checkpointer=None):
    settings = get_settings()
    return create_agent(
        model=f"openai:{settings.llm_model}",
        tools=TOOLS,
        system_prompt=get_system_prompt(),
        response_format=AgentAnswer,
        middleware=_middleware(),
        checkpointer=checkpointer or InMemorySaver(),
        name="accounting_research_assistant",
    )
