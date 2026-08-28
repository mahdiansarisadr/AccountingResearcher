"""Load MLflow's MCP tools for the current user's tracking store."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from ..core.workspace import current, tracking_uri

logger = logging.getLogger("mleng.mcp")


def load_mlflow_mcp_tools() -> list[Any]:
    """Stdio MCP client pointed at this user's MLflow file store.

    Returns an empty list if the server cannot start, so chat still works.
    """
    try:
        ctx = current()
    except RuntimeError:
        logger.warning("skipping MLflow MCP: no run context")
        return []

    uri = tracking_uri(ctx.user_id, data_dir=ctx.data_dir)
    env = {
        **os.environ,
        "MLFLOW_TRACKING_URI": uri,
        "MLFLOW_MCP_TOOLS": "ml",
    }

    async def _load() -> list[Any]:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {
                "mlflow": {
                    "command": "mlflow",
                    "args": ["mcp", "run"],
                    "transport": "stdio",
                    "env": env,
                }
            }
        )
        return list(await client.get_tools())

    try:
        return asyncio.run(_load())
    except RuntimeError:
        # Already inside an event loop (unusual for the worker). Fall back.
        logger.exception("could not load MLflow MCP tools")
        return []
    except Exception:
        logger.exception("could not load MLflow MCP tools")
        return []
