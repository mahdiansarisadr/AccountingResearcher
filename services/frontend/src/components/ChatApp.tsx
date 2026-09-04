"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelRun,
  createThread,
  deleteThread,
  getProgress,
  listExperiments,
  listMessages,
  listThreadFiles,
  listThreads,
  startRun,
  uploadThreadFile,
} from "@/lib/api";
import { streamRun } from "@/lib/sse";
import {
  ApiError,
  type ExperimentRun,
  type Message,
  type Progress,
  type Thread,
  type ThreadFile,
  type User,
} from "@/lib/types";

import { AppHeader } from "./AppHeader";
import { ChatPane, type LiveStep, type LiveTurn } from "./ChatPane";
import { ExperimentSidebar } from "./ExperimentSidebar";
import { ThreadSidebar } from "./ThreadSidebar";

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** Name a tool call the way the person watching would describe it. */
function describeTool(
  name: string,
  args: Record<string, unknown>,
): { label: string; detail: string | null } {
  if (name === "profile_dataset") {
    const file = text(args.filename);
    return { label: file ? `Profiling ${file}` : "Profiling the dataset", detail: null };
  }

  if (name === "train_model") {
    const detail = text(args.hypothesis);
    const rerun = args.recipe_version;
    if (typeof rerun === "number") {
      return { label: `Re-running v${rerun}`, detail };
    }
    const parent = args.parent_version;
    const what = text(args.model) ?? (args.code ? "custom code" : "the default model");
    const from = typeof parent === "number" ? ` from v${parent}` : "";
    return { label: `Training ${what}${from}`, detail };
  }

  if (name === "get_recipe") {
    const version = args.version;
    return {
      label: typeof version === "number" ? `Reading v${version}` : "Reading a recipe",
      detail: null,
    };
  }

  if (name === "report_progress") {
    return { label: "Reviewing the whole search", detail: null };
  }

  return { label: name, detail: null };
}

/** The opening message an upload stands in for. */
function kickoff(filename: string): string {
  return (
    `I've uploaded ${filename}. Profile it and tell me what's in it. ` +
    `If it's clear what I'd want to predict, go ahead and start working on it ` +
    `and keep iterating until it stops getting better — otherwise ask me first.`
  );
}

export function ChatApp({ user }: { user: User }) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [files, setFiles] = useState<ThreadFile[]>([]);
  const [experiments, setExperiments] = useState<ExperimentRun[]>([]);
  const [progress, setProgress] = useState<Progress | null>(null);
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

  const refreshResults = useCallback(async (threadId: string) => {
    const [runs, trajectory] = await Promise.all([
      listExperiments(threadId).catch(() => []),
      getProgress(threadId).catch(() => null),
    ]);
    setExperiments(runs);
    setProgress(trajectory);
  }, []);

  useEffect(() => {
    void refreshThreads();
    return () => stopStream.current?.();
  }, [refreshThreads]);

  async function openThread(id: string) {
    setSelectedId(id);
    setMessages(await listMessages(id));
    setFiles(await listThreadFiles(id).catch(() => []));
    await refreshResults(id);
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
        setExperiments([]);
      }
    }
  }

  /** Queue a turn and stream it. Shared by typing a message and dropping a file. */
  async function launch(threadId: string, question: string) {
    setLive({
      question,
      tokens: "",
      answer: null,
      steps: [],
      startedAt: Date.now(),
      error: null,
    });
    setBusy(true);

    try {
      const run = await startRun(threadId, question);
      setRunId(run.run_id);
      setMessages(await listMessages(threadId));
      await refreshThreads();

      stopStream.current = streamRun(run.run_id, {
        onToken(chunk) {
          setLive((current) =>
            current ? { ...current, tokens: current.tokens + chunk } : current,
          );
        },
        onToolCall(name, args) {
          setLive((current) => {
            if (!current) return current;
            const { label, detail } = describeTool(name, args);
            const step: LiveStep = {
              id: current.steps.length,
              label,
              detail,
              result: null,
              status: "running",
            };
            return { ...current, steps: [...current.steps, step] };
          });
        },
        onToolResult(name, ok, summary) {
          // Results arrive in call order, so the oldest unfinished step is this one.
          setLive((current) => {
            if (!current) return current;
            const pending = current.steps.findIndex(
              (step) => step.status === "running",
            );
            if (pending === -1) return current;
            const steps = [...current.steps];
            steps[pending] = {
              ...steps[pending],
              status: ok ? "ok" : "failed",
              result: summary || null,
            };
            return { ...current, steps };
          });
          // A search can run for many minutes. Show each version as it lands
          // rather than making the panel wait for the whole thing to finish.
          if (name === "train_model") void refreshResults(threadId);
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
          await refreshResults(threadId);
          await refreshThreads();
        },
      });
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.status === 409
            ? "This conversation already has a run in progress. Wait for it, or refresh."
            : error.message
          : "Could not start the run.";
      setLive((current) => (current ? { ...current, error: message } : current));
      setRunId(null);
    } finally {
      setBusy(false);
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
    await launch(threadId, text);
  }

  async function onAttach(file: File) {
    if (streaming) return;

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
        error instanceof ApiError ? error.message : "Could not upload the file.";
      setLive({
        question: `Uploaded ${file.name}`,
        tokens: "",
        answer: null,
        steps: [],
        startedAt: Date.now(),
        error: message,
      });
      return;
    } finally {
      setBusy(false);
    }

    // An upload is a request to get to work. The agent profiles the file and
    // either starts or asks, rather than waiting to be told twice.
    await launch(threadId, kickoff(file.name));
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
          emptyHint="Drop a CSV and I'll get to work on it — or just chat."
        />
        <ExperimentSidebar
          runs={experiments}
          progress={progress}
          visible={selectedId !== null}
        />
      </div>
    </div>
  );
}
