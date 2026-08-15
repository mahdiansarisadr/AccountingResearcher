"use client";

import { useEffect, useState } from "react";

import { listUsers, updateUser } from "@/lib/api";
import { ApiError, type User } from "@/lib/types";

import { AppHeader } from "./AppHeader";

export function AdminUsers({ me }: { me: User }) {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setUsers(await listUsers());
  }

  useEffect(() => {
    void refresh().catch((caught: unknown) => {
      setError(caught instanceof ApiError ? caught.message : "Could not load users.");
    });
  }, []);

  async function patch(user: User, next: { role?: User["role"]; is_active?: boolean }) {
    setError(null);
    try {
      const updated = await updateUser(user.id, next);
      setUsers((current) => current.map((row) => (row.id === updated.id ? updated : row)));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Update failed.");
    }
  }

  return (
    <div className="flex h-full flex-col">
      <AppHeader user={me} />
      <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-8">
        <h1 className="font-serif text-3xl">Users</h1>
        <p className="mt-2 text-sm text-ink-muted">
          Anyone on the company domain can sign in. What you do here is promote
          someone, or take access away.
        </p>
        {error ? <p className="mt-4 text-sm text-red-800">{error}</p> : null}
        <table className="mt-6 w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-ink-faint">
            <tr>
              <th className="py-2 font-medium">Email</th>
              <th className="py-2 font-medium">Role</th>
              <th className="py-2 font-medium">Access</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const self = user.id === me.id;
              return (
                <tr key={user.id} className="border-t border-black/10">
                  <td className="py-3">
                    <div>{user.email}</div>
                    {user.name ? (
                      <div className="text-xs text-ink-faint">{user.name}</div>
                    ) : null}
                  </td>
                  <td className="py-3">
                    <select
                      value={user.role}
                      disabled={self}
                      onChange={(event) =>
                        void patch(user, {
                          role: event.target.value as User["role"],
                        })
                      }
                      className="rounded border border-black/10 bg-paper-raised px-2 py-1 disabled:opacity-50"
                    >
                      <option value="member">member</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td className="py-3">
                    <button
                      type="button"
                      disabled={self}
                      onClick={() => void patch(user, { is_active: !user.is_active })}
                      className={`rounded px-2 py-1 text-xs ${
                        user.is_active
                          ? "bg-ink/10 text-ink"
                          : "bg-red-100 text-red-900"
                      } disabled:opacity-50`}
                    >
                      {user.is_active ? "Active" : "Deactivated"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </main>
    </div>
  );
}
