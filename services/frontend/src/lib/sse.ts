import { API_URL } from "./api";
import type { AgentAnswer } from "./types";

export type StreamHandlers = {
  onToken?: (text: string) => void;
  onToolCall?: (name: string, args: Record<string, unknown>) => void;
  onToolResult?: (name: string, ok: boolean, summary: string) => void;
  onAnswer?: (answer: AgentAnswer) => void;
  onError?: (message: string) => void;
  onDone?: (status: string) => void;
};

function parseData<T>(event: MessageEvent): T | null {
  try {
    return JSON.parse(event.data) as T;
  } catch {
    return null;
  }
}

export function streamRun(runId: string, handlers: StreamHandlers): () => void {
  // withCredentials so the session cookie is sent on this cross-origin stream.
  const source = new EventSource(`${API_URL}/runs/${runId}/stream`, {
    withCredentials: true,
  });
  let closed = false;

  function finish(status: string) {
    if (closed) return;
    closed = true;
    source.close();
    handlers.onDone?.(status);
  }

  source.addEventListener("token", (event) => {
    const data = parseData<{ text: string }>(event);
    if (data?.text) handlers.onToken?.(data.text);
  });
  source.addEventListener("tool_call", (event) => {
    const data = parseData<{ name: string; args: Record<string, unknown> }>(event);
    if (data?.name) handlers.onToolCall?.(data.name, data.args ?? {});
  });
  source.addEventListener("tool_result", (event) => {
    const data = parseData<{ name: string; ok: boolean; summary: string }>(event);
    if (data?.name) handlers.onToolResult?.(data.name, data.ok, data.summary);
  });
  source.addEventListener("answer", (event) => {
    const data = parseData<{ answer: AgentAnswer }>(event);
    if (data?.answer) handlers.onAnswer?.(data.answer);
  });
  source.addEventListener("error", (event) => {
    if (event instanceof MessageEvent) {
      const data = parseData<{ message: string }>(event);
      if (data?.message) handlers.onError?.(data.message);
    }
  });
  source.addEventListener("done", (event) => {
    const data = parseData<{ status: string }>(event);
    finish(data?.status ?? "succeeded");
  });
  source.onerror = () => {
    // A transport failure (not an `event: error`). The run may still finish;
    // the caller reloads messages on close either way.
    finish("failed");
  };

  return () => {
    closed = true;
    source.close();
  };
}
