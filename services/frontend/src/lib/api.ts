import { ApiError, type Message, type Run, type Thread, type User, type UserRole } from "./types";

// In production Caddy serves UI and API on one host, so this is https://{PUBLIC_HOST}.
// `??` only falls through on null/undefined; an explicit empty string means same-origin.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function loginUrl(): string {
  return `${API_URL}/auth/login`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    headers,
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof payload?.detail === "string" ? payload.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

export function getMe(): Promise<User> {
  return request("/me");
}

export function logout(): Promise<void> {
  return request("/auth/logout", { method: "POST" });
}

export function listThreads(): Promise<Thread[]> {
  return request("/threads");
}

export function createThread(title?: string): Promise<Thread> {
  return request("/threads", {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
  });
}

export function deleteThread(threadId: string): Promise<void> {
  return request(`/threads/${threadId}`, { method: "DELETE" });
}

export function listMessages(threadId: string): Promise<Message[]> {
  return request(`/threads/${threadId}/messages`);
}

export function startRun(threadId: string, message: string): Promise<Run> {
  return request(`/threads/${threadId}/runs`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function cancelRun(runId: string): Promise<Run> {
  return request(`/runs/${runId}/cancel`, { method: "POST" });
}

export function listUsers(): Promise<User[]> {
  return request("/admin/users");
}

export function updateUser(
  userId: string,
  patch: { role?: UserRole; is_active?: boolean },
): Promise<User> {
  return request(`/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}
