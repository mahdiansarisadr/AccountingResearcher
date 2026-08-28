"""Agent tools, exported as a single list for create_agent."""

from __future__ import annotations

from .profile import profile_dataset
from .train import train_model

LOCAL_TOOLS = [profile_dataset, train_model]
TOOLS = LOCAL_TOOLS

__all__ = ["LOCAL_TOOLS", "TOOLS", "profile_dataset", "train_model"]
