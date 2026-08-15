"use client";

import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

import type { AgentAnswer, Message } from "@/lib/types";

export type LiveTurn = {
  question: string;
  tokens: string;
  answer: AgentAnswer | null;
  tools: string[];
  error: string | null;
};

function asAnswer(payload: Message["payload"]): AgentAnswer | null {
  if (!payload || typeof payload !== "object") return null;
  if (!("answer" in payload) || typeof payload.answer !== "string") return null;
  return payload as AgentAnswer;
}

function Citations({ answer }: { answer: AgentAnswer }) {
  if (!answer.citations.length && !answer.sql_used) return null;
  return (
    <details className="mt-3 text-xs text-ink-muted">
      <summary className="cursor-pointer select-none">
        {answer.abstained ? "Why this was declined" : "Sources"}
        {Number.isFinite(answer.confidence)
          ? ` · ${Math.round(answer.confidence * 100)}%`
          : ""}
      </summary>
      <ul className="mt-2 space-y-1">
        {answer.citations.map((citation, index) => (
          <li key={`${citation.source_file}-${citation.locator}-${index}`}>
            <span className="font-medium">{citation.source_file}</span>
            <span className="text-ink-faint"> {citation.locator}</span>
            {citation.snippet ? (
              <span className="block text-ink-faint">{citation.snippet}</span>
            ) : null}
          </li>
        ))}
      </ul>
      {answer.sql_used ? (
        <pre className="mt-2 overflow-x-auto rounded bg-ink/5 p-2 font-mono text-[11px] leading-5">
          {answer.sql_used}
        </pre>
      ) : null}
    </details>
  );
}

function Bubble({
  align,
  children,
}: {
  align: "start" | "end";
  children: ReactNode;
}) {
  return (
    <div className={`flex ${align === "end" ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[42rem] rounded-2xl px-4 py-3 text-[15px] leading-6 ${
          align === "end"
            ? "bg-ink text-paper"
            : "bg-paper-raised shadow-sm ring-1 ring-black/5"
        }`}
      >
        {children}
      </div>
    </div>
  );
}

export function ChatPane({
  messages,
  live,
  draft,
  onDraft,
  onSend,
  onCancel,
  streaming,
  emptyHint,
}: {
  messages: Message[];
  live: LiveTurn | null;
  draft: string;
  onDraft: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
  streaming: boolean;
  emptyHint: string;
}) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, live]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  }

  const liveQuestionAlreadyStored =
    live !== null && messages.some((message) => message.content === live.question);

  return (
    <section className="flex min-w-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        {messages.length === 0 && !live ? (
          <p className="mx-auto max-w-md pt-24 text-center font-serif text-xl text-ink-muted">
            {emptyHint}
          </p>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {messages
              .filter((message) => message.role !== "tool")
              .map((message) => {
                const answer = asAnswer(message.payload);
                return (
                  <Bubble
                    key={message.id}
                    align={message.role === "user" ? "end" : "start"}
                  >
                    <p className="whitespace-pre-wrap">
                      {answer?.answer ?? message.content}
                    </p>
                    {answer ? <Citations answer={answer} /> : null}
                  </Bubble>
                );
              })}
            {live && !liveQuestionAlreadyStored ? (
              <Bubble align="end">
                <p className="whitespace-pre-wrap">{live.question}</p>
              </Bubble>
            ) : null}
            {live ? (
              <Bubble align="start">
                {live.tools.length ? (
                  <p className="mb-2 text-xs uppercase tracking-wide text-ink-faint">
                    {live.tools[live.tools.length - 1]}
                  </p>
                ) : null}
                <p className="whitespace-pre-wrap">
                  {live.answer?.answer || live.tokens || (streaming ? "…" : "")}
                </p>
                {live.answer ? <Citations answer={live.answer} /> : null}
                {live.error ? (
                  <p className="mt-2 text-sm text-red-800">{live.error}</p>
                ) : null}
              </Bubble>
            ) : null}
            <div ref={endRef} />
          </div>
        )}
      </div>
      <form
        className="border-t border-black/10 bg-paper-raised px-4 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          onSend();
        }}
      >
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            value={draft}
            onChange={(event) => onDraft(event.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Ask a question about the accounts…"
            disabled={streaming}
            className="min-h-[3rem] flex-1 resize-none rounded-xl border border-black/10 bg-paper px-3 py-2 text-sm outline-none focus:border-copper"
          />
          {streaming ? (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-xl bg-ink px-4 py-2 text-sm text-paper hover:bg-ink-muted"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!draft.trim()}
              className="rounded-xl bg-copper px-4 py-2 text-sm text-white hover:bg-copper-dark disabled:opacity-40"
            >
              Send
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
