"use client";

import { useRouter } from "next/navigation";

import { AdminUsers } from "@/components/AdminUsers";
import { useCurrentUser } from "@/lib/useCurrentUser";

export default function AdminPage() {
  const router = useRouter();
  const { user, loading } = useCurrentUser();

  if (loading || !user) {
    return <p className="p-8 text-sm text-ink-muted">Loading…</p>;
  }

  if (user.role !== "admin") {
    router.replace("/");
    return null;
  }

  return <AdminUsers me={user} />;
}
