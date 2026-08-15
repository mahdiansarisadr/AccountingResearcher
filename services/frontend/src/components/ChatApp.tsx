"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelRun,
  createThread,
  deleteThread,
  listMessages,
  listThreads,
  startRun,
} from "@/lib/api";
import { streamRun } from "@/lib/sse";
import { ApiError, type Message, type Thread, type User } from "@/lib/types";

import { AppHeader } from "./AppHeader";
import { ChatPane, type LiveTurn } from "./ChatPane";
import { ThreadSidebar } from "./ThreadSidebar";

function describeTool(name: string): string {
  if (name === "search_schema") return "Selecting tables…";
  if (name === "run_sql_query") return "Running SQL…";
  return name;
}

export function ChatApp({ user }: { user: User }) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const stopStream = useRef<(() => void) | null>(null);

  const streaming = runId !== null;

  const refreshThreads = useCallback(async () => {
    const listed = await listThreads();
    setThreads(listed);
    return listed;
  }, []);

  useEffect(() => {
    void refreshThreads();
    return () => stopStream.current?.();
  }, [refreshThreads]);

  async function openThread(id: string) {
    setSelectedId(id);
    setMessages(await listMessages(id));
    setLive(null);
  }

  async function onCreate() {
    const thread = await createThread();
    await refreshThreads();
    await openThread(thread.id);
  }

  async function onDelete(id: string) {
    await deleteThread(id);
    const listed = await refreshThreads();
    if (selectedId === id) {
      const next = listed[0];
      if (next) await openThread(next.id);
      else {
        setSelectedId(null);
        setMessages([]);
      }
    }
  }

  async function onSend() {
    const text = draft.trim();
    if (!text || streaming) return;

    let threadId = selectedId;
    if (!threadId) {
      const thread = await createThread();
      threadId = thread.id;
      setSelectedId(threadId);
      await refreshThreads();
    }

    setDraft("");
    setLive({ question: text, tokens: "", answer: null, tools: [], error: null });
    setBusy(true);

    try {
      const run = await startRun(threadId, text);
      setRunId(run.run_id);
      setMessages(await listMessages(threadId));
      await refreshThreads();

      stopStream.current = streamRun(run.run_id, {
        onToken(chunk) {
          setLive((current) =>
            current ? { ...current, tokens: current.tokens + chunk } : current,
          );
        },
        onToolCall(name) {
          setLive((current) =>
            current
              ? { ...current, tools: [...current.tools, describeTool(name)] }
              : current,
          );
        },
        onAnswer(answer) {
          setLive((current) => (current ? { ...current, answer } : current));
        },
        onError(message) {
          setLive((current) => (current ? { ...current, error: message } : current));
        },
        async onDone() {
          stopStream.current = null;
          setRunId(null);
          setMessages(await listMessages(threadId));
          setLive(null);
          await refreshThreads();
        },
      });
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : "Could not start the run.";
      setLive((current) => (current ? { ...current, error: message } : current));
      setRunId(null);
    } finally {
      setBusy(false);
    }
  }

  async function onCancel() {
    if (!runId) return;
    await cancelRun(runId).catch(() => undefined);
  }

  return (
    <div className="flex h-full flex-col">
      <AppHeader user={user} />
      <div className="flex min-h-0 flex-1">
        <ThreadSidebar
          threads={threads}
          selectedId={selectedId}
          onSelect={(id) => void openThread(id)}
          onCreate={() => void onCreate()}
          onDelete={(id) => void onDelete(id)}
          busy={busy || streaming}
        />
        <ChatPane
          messages={messages}
          live={live}
          draft={draft}
          onDraft={setDraft}
          onSend={() => void onSend()}
          onCancel={() => void onCancel()}
          streaming={streaming}
          emptyHint="Ask a question. Answers are grounded in the accounting store, and cited."
        />
      </div>
    </div>
  );
}
