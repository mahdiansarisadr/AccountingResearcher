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

export type ExperimentRun = {
  run_id: string;
  name: string;
  status: string;
  started_at: string | null;
  model: string | null;
  task: string | null;
  hypothesis: string | null;
  primary_metric: string | null;
  primary_value: number | null;
  metrics: Record<string, number>;
  recipe_version: number | null;
  recipe_parent: number | null;
  recipe_kind: string | null;
  reused: boolean;
  split_seed: string | null;
  error: string | null;
};

export type ProgressStep = {
  order: number;
  version: number | null;
  run_id: string;
  at: string | null;
  value: number | null;
  best_so_far: number | null;
  improved: boolean;
  gain: number | null;
  note: string | null;
  failed: boolean;
  error: string | null;
};

export type Progress = {
  metric: string | null;
  steps: ProgressStep[];
  first: number | null;
  best: number | null;
  best_version: number | null;
  total_gain: number | null;
  versions: number;
  runs: number;
  failed: number;
  noise: number | null;
  seconds: number;
  improved: boolean;
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
