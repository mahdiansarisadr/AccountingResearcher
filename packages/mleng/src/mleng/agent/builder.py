"""Assemble the MLEng agent with create_agent."""

from __future__ import annotations

import logging

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import InMemorySaver

from ..core.settings import get_settings
from .mcp import load_mlflow_mcp_tools
from .prompts import get_system_prompt
from .schemas import AgentAnswer
from .tools import LOCAL_TOOLS

logger = logging.getLogger("mleng.agent")


def _middleware() -> list:
    return [
        ModelRetryMiddleware(max_retries=2, initial_delay=0.5, max_delay=4.0),
        ToolRetryMiddleware(max_retries=1, initial_delay=0.3, max_delay=2.0),
        ModelCallLimitMiddleware(run_limit=24, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=30, exit_behavior="end"),
        PIIMiddleware("email", strategy="mask", apply_to_tool_results=True),
    ]


def build_agent(*, checkpointer=None, include_mcp: bool | None = None):
    """Build an agent bound to the current run context (user + thread).

    Call :func:`~mleng.core.workspace.set_run_context` first so tools and the
    MLflow MCP server see this user's directory only.
    """
    settings = get_settings()
    model_name = settings.llm_model.removeprefix("anthropic:")
    tools = list(LOCAL_TOOLS)
    use_mcp = settings.enable_mlflow_mcp if include_mcp is None else include_mcp
    if use_mcp:
        mcp_tools = load_mlflow_mcp_tools()
        if mcp_tools:
            tools.extend(mcp_tools)
            logger.info("loaded %s MLflow MCP tools", len(mcp_tools))
        else:
            logger.warning("MLflow MCP tools unavailable; continuing with local tools")

    return create_agent(
        model=ChatAnthropic(model=model_name, api_key=settings.anthropic_api_key),
        tools=tools,
        system_prompt=get_system_prompt(),
        response_format=AgentAnswer,
        middleware=_middleware(),
        checkpointer=checkpointer or InMemorySaver(),
        name="mleng_assistant",
    )
