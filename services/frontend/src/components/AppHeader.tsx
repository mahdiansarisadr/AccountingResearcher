"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { logout } from "@/lib/api";
import type { User } from "@/lib/types";

export function AppHeader({ user }: { user: User }) {
  const router = useRouter();

  async function onSignOut() {
    await logout().catch(() => undefined);
    router.replace("/login");
  }

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-black/10 bg-paper-raised px-4">
      <Link href="/" className="font-serif text-lg tracking-tight">
        MLEng
      </Link>
      <nav className="flex items-center gap-4 text-sm">
        {user.role === "admin" ? (
          <Link href="/admin" className="text-ink-muted hover:text-ink">
            Users
          </Link>
        ) : null}
        <span className="hidden text-ink-faint sm:inline">{user.email}</span>
        <button
          type="button"
          onClick={onSignOut}
          className="text-ink-muted hover:text-ink"
        >
          Sign out
        </button>
      </nav>
    </header>
  );
}
