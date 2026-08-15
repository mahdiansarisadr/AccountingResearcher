"use client";

import { ChatApp } from "@/components/ChatApp";
import { useCurrentUser } from "@/lib/useCurrentUser";

export default function HomePage() {
  const { user, loading } = useCurrentUser();

  if (loading || !user) {
    return <p className="p-8 text-sm text-ink-muted">Loading…</p>;
  }

  return <ChatApp user={user} />;
}
