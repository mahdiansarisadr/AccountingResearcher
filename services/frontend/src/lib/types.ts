export type UserRole = "admin" | "member";

export type User = {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
};

export type Thread = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type AgentAnswer = {
  answer: string;
  confidence: number;
  abstained: boolean;
  reason: string | null;
};

export type Message = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  payload: AgentAnswer | Record<string, unknown> | null;
  created_at: string;
};

export type ThreadFile = {
  name: string;
  size: number;
  modified_at: string;
};

export type Run = {
  run_id: string;
  thread_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
