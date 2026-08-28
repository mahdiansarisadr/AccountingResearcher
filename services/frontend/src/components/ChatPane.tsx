"use client";

import { useEffect, useRef, type ClipboardEvent, type DragEvent, type KeyboardEvent, type ReactNode } from "react";

import type { AgentAnswer, Message, ThreadFile } from "@/lib/types";

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

function AnswerMeta({ answer }: { answer: AgentAnswer }) {
  if (!answer.abstained && !Number.isFinite(answer.confidence)) return null;
  return (
    <p className="mt-3 text-xs text-ink-muted">
      {answer.abstained
        ? `Declined${answer.reason ? `: ${answer.reason}` : ""}`
        : `${Math.round(answer.confidence * 100)}% confidence`}
    </p>
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
  onAttach,
  files,
  streaming,
  emptyHint,
}: {
  messages: Message[];
  live: LiveTurn | null;
  draft: string;
  onDraft: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
  onAttach: (file: File) => void;
  files: ThreadFile[];
  streaming: boolean;
  emptyHint: string;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, live]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  }

  function takeTableFile(list: FileList | null): File | null {
    const chosen = list?.[0];
    if (!chosen) return null;
    const name = chosen.name.toLowerCase();
    if (name.endsWith(".csv") || name.endsWith(".parquet") || name.endsWith(".pq")) {
      return chosen;
    }
    if (chosen.type === "text/csv" || chosen.type === "text/plain") {
      return chosen;
    }
    return null;
  }

  function onDrop(event: DragEvent<HTMLFormElement>) {
    event.preventDefault();
    if (streaming) return;
    const file = takeTableFile(event.dataTransfer.files);
    if (file) onAttach(file);
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    if (streaming) return;
    const file = takeTableFile(event.clipboardData.files);
    if (file) {
      event.preventDefault();
      onAttach(file);
      return;
    }
    const text = event.clipboardData.getData("text/plain");
    if (text.length > 8_000) {
      event.preventDefault();
      onAttach(new File([text], "pasted.csv", { type: "text/csv" }));
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
                    {answer ? <AnswerMeta answer={answer} /> : null}
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
                {live.answer ? <AnswerMeta answer={live.answer} /> : null}
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
        onDragOver={(event) => event.preventDefault()}
        onDrop={onDrop}
      >
        {files.length ? (
          <p className="mx-auto mb-2 max-w-3xl text-xs text-ink-muted">
            {files.map((file) => file.name).join(" · ")}
          </p>
        ) : null}
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.parquet,.pq,text/csv"
            className="hidden"
            onChange={(event) => {
              const chosen = event.target.files?.[0];
              if (chosen) onAttach(chosen);
              event.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={streaming}
            className="rounded-xl border border-black/10 px-3 py-2 text-sm text-ink-muted hover:text-ink disabled:opacity-40"
          >
            Attach
          </button>
          <textarea
            value={draft}
            onChange={(event) => onDraft(event.target.value)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            rows={2}
            placeholder="Attach a CSV, then ask to train…"
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
