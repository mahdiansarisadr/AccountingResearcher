"""CLI chat for MLEng.

A thin renderer over :func:`mleng.agent.runner.run_agent`. The CLI owns no
execution logic — it turns run events into terminal output, the same way the
web layer turns them into SSE.
"""

from __future__ import annotations

import argparse
import sys

from ..agent.builder import build_agent
from ..agent.runner import Message, run_agent
from ..agent.schemas import AgentAnswer
from ..core.workspace import reset_run_context, set_run_context

CLI_USER = "cli"
CLI_THREAD = "local"


def _with_cli_context():
    return set_run_context(CLI_USER, CLI_THREAD)


def _preview(args: dict) -> str:
    text = str(args.get("target") or args.get("filename") or args.get("query") or "")
    return (text[:80] + "...") if len(text) > 80 else text


def _render_metadata(answer: AgentAnswer) -> None:
    if answer.abstained:
        print("\n  [abstained]", end="")
        if answer.reason:
            print(f" {answer.reason}", end="")
        print()
    print(f"\n  confidence: {answer.confidence:.2f}")


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
    if answer is None:
        return []
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer.answer},
    ]


def run_once(question: str) -> int:
    token = _with_cli_context()
    try:
        answer = _run_turn(question, [], build_agent())
        if answer is not None:
            _render_metadata(answer)
        return 0
    finally:
        reset_run_context(token)


def run_interactive() -> int:
    token = _with_cli_context()
    try:
        agent = build_agent()
        history: list[Message] = []
        print("MLEng. Type 'exit' to quit.\n")
        print("CLI uploads live in data/users/cli/uploads/local/\n")
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
    finally:
        reset_run_context(token)


def main() -> None:
    parser = argparse.ArgumentParser(description="MLEng CLI")
    parser.add_argument("--ask", metavar="QUESTION", help="ask a single question and exit")
    args = parser.parse_args()
    if args.ask:
        sys.exit(run_once(args.ask))
    sys.exit(run_interactive())


if __name__ == "__main__":
    main()
