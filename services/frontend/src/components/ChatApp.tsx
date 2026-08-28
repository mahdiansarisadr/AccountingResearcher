"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelRun,
  createThread,
  deleteThread,
  listMessages,
  listThreadFiles,
  listThreads,
  startRun,
  uploadThreadFile,
} from "@/lib/api";
import { streamRun } from "@/lib/sse";
import { ApiError, type Message, type Thread, type ThreadFile, type User } from "@/lib/types";

import { AppHeader } from "./AppHeader";
import { ChatPane, type LiveTurn } from "./ChatPane";
import { ThreadSidebar } from "./ThreadSidebar";

function describeTool(name: string): string {
  if (name === "profile_dataset") return "Profiling dataset…";
  if (name === "train_model") return "Training model…";
  if (name.startsWith("search_") || name.includes("trace") || name.includes("run")) {
    return `MLflow: ${name}`;
  }
  return name;
}

export function ChatApp({ user }: { user: User }) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [files, setFiles] = useState<ThreadFile[]>([]);
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
    setFiles(await listThreadFiles(id).catch(() => []));
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
        setFiles([]);
      }
    }
  }

  async function onSend() {
    const text = draft.trim();
    if (!text || streaming) return;

    if (text.length > 4_000) {
      setDraft("");
      await onAttach(new File([text], "pasted.csv", { type: "text/csv" }));
      return;
    }

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

  async function onAttach(file: File) {
    let threadId = selectedId;
    if (!threadId) {
      const thread = await createThread();
      threadId = thread.id;
      setSelectedId(threadId);
      await refreshThreads();
    }
    setBusy(true);
    try {
      await uploadThreadFile(threadId, file);
      setFiles(await listThreadFiles(threadId));
    } catch (error) {
      const message =
        error instanceof ApiError && error.status === 413
          ? "File is too large (max 25 MB). Attach a CSV or Parquet file — don’t paste the table into the chat."
          : error instanceof ApiError
            ? error.message
            : "Could not upload the file.";
      setLive({
        question: `Uploaded ${file.name}`,
        tokens: "",
        answer: null,
        tools: [],
        error: message,
      });
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
          onAttach={(file) => void onAttach(file)}
          files={files}
          streaming={streaming}
          emptyHint="Upload a CSV, then ask me to train a model — or just chat."
        />
      </div>
    </div>
  );
}
