"""CLI chat interface for the Accounting Research Assistant.

Usage:
    ar-chat                  interactive chat
    ar-chat --ask "..."      one-shot question
    ar-chat --smoke          run the 3 core question types
"""

from __future__ import annotations

import argparse
import sys
from uuid import uuid4

from ..agent.builder import build_agent
from ..agent.schemas import AgentAnswer

SMOKE_QUESTIONS = [
    "What is the total travel cost for the Finance team over the last 3 years?",
    "What is the monthly spending trend since the start of 2026?",
    "Which audit cases have not been audited yet?",
]


def _print_progress(message) -> None:
    mtype = getattr(message, "type", None)
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            args = tc.get("args", {})
            preview = args.get("query") or args.get("sql") or ""
            preview = (preview[:80] + "...") if len(str(preview)) > 80 else preview
            print(f"  \u2192 {tc['name']}: {preview}")
    elif mtype == "tool":
        name = getattr(message, "name", "tool")
        content = str(getattr(message, "content", ""))
        note = "error" if content.startswith("ERROR") else f"{len(content)} chars"
        print(f"  \u2190 {name} returned ({note})")


def _render(answer: AgentAnswer | None) -> None:
    if answer is None:
        print("\n[unavailable] The agent could not produce a grounded answer "
              "within its step budget. Please rephrase or try again.")
        return

    print()
    if answer.abstained:
        print(f"[abstained] {answer.answer}")
        if answer.reason:
            print(f"  reason: {answer.reason}")
    else:
        print(answer.answer)

    print(f"\n  confidence: {answer.confidence:.2f}")
    if answer.citations:
        print("  citations:")
        for c in answer.citations:
            snip = f" — {c.snippet}" if c.snippet else ""
            print(f"    - {c.source_file} ({c.locator}){snip}")
    if answer.sql_used:
        print(f"  sql: {answer.sql_used}")


def _run_turn(agent, text: str, config: dict) -> AgentAnswer | None:
    final = None
    printed = 0
    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": text}]},
        config=config,
        stream_mode="values",
    ):
        final = chunk
        messages = chunk.get("messages", [])
        for message in messages[printed:]:
            _print_progress(message)
        printed = len(messages)

    if final is None:
        return None
    return final.get("structured_response")


def _new_config() -> dict:
    return {"configurable": {"thread_id": str(uuid4())}}


def run_smoke() -> int:
    agent = build_agent()
    ok = True
    for i, question in enumerate(SMOKE_QUESTIONS, start=1):
        print(f"\n{'=' * 70}\n[{i}] {question}\n{'=' * 70}")
        answer = _run_turn(agent, question, _new_config())
        _render(answer)
        if answer is None or (answer.abstained and answer.confidence < 0.3):
            ok = False
    print(f"\n{'=' * 70}\nSmoke test: {'PASS' if ok else 'CHECK OUTPUT'}")
    return 0 if ok else 1


def run_once(question: str) -> int:
    agent = build_agent()
    answer = _run_turn(agent, question, _new_config())
    _render(answer)
    return 0


def run_interactive() -> int:
    agent = build_agent()
    config = _new_config()  # one thread for the whole session (multi-turn memory)
    print("Accounting Research Assistant (Phase 1). Type 'exit' to quit.\n")
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            break
        answer = _run_turn(agent, question, config)
        _render(answer)
        print()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Accounting Research Assistant CLI")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ask", metavar="QUESTION", help="ask a single question and exit")
    group.add_argument("--smoke", action="store_true", help="run the 3 core question types")
    args = parser.parse_args()

    if args.smoke:
        sys.exit(run_smoke())
    if args.ask:
        sys.exit(run_once(args.ask))
    sys.exit(run_interactive())


if __name__ == "__main__":
    main()
