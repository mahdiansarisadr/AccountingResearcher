"""CLI chat interface for the Accounting Research Assistant.

A thin renderer over :func:`accounting_research.agent.runner.run_agent`. The CLI
deliberately owns no execution logic — it turns run events into terminal output,
exactly as the web layer turns the same events into SSE.

Usage:
    ar-chat                  interactive chat
    ar-chat --ask "..."      one-shot question
    ar-chat --smoke          run the 3 core question types
"""

from __future__ import annotations

import argparse
import sys

from ..agent.builder import build_agent
from ..agent.runner import Message, run_agent
from ..agent.schemas import AgentAnswer

SMOKE_QUESTIONS = [
    "What is the total travel cost for the Finance team over the last 3 years?",
    "What is the monthly spending trend since the start of 2026?",
    "Which audit cases have not been audited yet?",
]


def _preview(args: dict) -> str:
    text = str(args.get("query") or args.get("sql") or "")
    return (text[:80] + "...") if len(text) > 80 else text


def _render_metadata(answer: AgentAnswer) -> None:
    """Print what streaming could not: the parts that only exist once complete."""
    if answer.abstained:
        print("\n  [abstained]", end="")
        if answer.reason:
            print(f" {answer.reason}", end="")
        print()

    print(f"\n  confidence: {answer.confidence:.2f}")
    if answer.citations:
        print("  citations:")
        for citation in answer.citations:
            snippet = f" \u2014 {citation.snippet}" if citation.snippet else ""
            print(f"    - {citation.source_file} ({citation.locator}){snippet}")
    if answer.sql_used:
        print(f"  sql: {answer.sql_used}")


def _run_turn(question: str, history: list[Message], agent) -> AgentAnswer | None:
    """Stream one turn to the terminal and return the structured answer."""
    answer: AgentAnswer | None = None
    streaming = False

    for event in run_agent(question, history=history, agent=agent):
        if event.type == "tool_call":
            print(f"  \u2192 {event.name}: {_preview(event.args)}")
        elif event.type == "tool_result":
            state = "" if event.ok else "error: "
            print(f"  \u2190 {event.name} returned ({state}{event.summary})")
        elif event.type == "token":
            if not streaming:
                print()
                streaming = True
            print(event.text, end="", flush=True)
        elif event.type == "answer":
            answer = event.answer
        elif event.type == "error":
            print(f"\n[error] {event.message}")

    if streaming:
        print()
    return answer


def _dialogue(question: str, answer: AgentAnswer | None) -> list[Message]:
    """The turn as history for the next question."""
    if answer is None:
        return []
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer.answer},
    ]


def run_smoke() -> int:
    agent = build_agent()
    ok = True
    for i, question in enumerate(SMOKE_QUESTIONS, start=1):
        print(f"\n{'=' * 70}\n[{i}] {question}\n{'=' * 70}")
        answer = _run_turn(question, [], agent)
        if answer is not None:
            _render_metadata(answer)
        if answer is None or (answer.abstained and answer.confidence < 0.3):
            ok = False
    print(f"\n{'=' * 70}\nSmoke test: {'PASS' if ok else 'CHECK OUTPUT'}")
    return 0 if ok else 1


def run_once(question: str) -> int:
    answer = _run_turn(question, [], build_agent())
    if answer is not None:
        _render_metadata(answer)
    return 0


def run_interactive() -> int:
    agent = build_agent()
    # The caller owns the conversation, mirroring how the web layer will pass
    # history loaded from Postgres once threads are persisted.
    history: list[Message] = []
    print("Accounting Research Assistant. Type 'exit' to quit.\n")
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
        answer = _run_turn(question, history, agent)
        if answer is not None:
            _render_metadata(answer)
        history.extend(_dialogue(question, answer))
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
