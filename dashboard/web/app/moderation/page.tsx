"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

interface ModItem {
  id: number;
  guild_id: number;
  target_id: number;
  target_name?: string | null;
  moderator_id: number;
  moderator_name?: string | null;
  action_type: string;
  reason: string | null;
  created_at: string;
}

interface ModResponse {
  items: ModItem[];
  total: number;
  page: number;
  page_size: number;
}

const ACTION_TYPES = ["kick", "ban", "unban", "timeout", "warn", "clear"];

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function actionBadgeClass(action: string): string {
  switch (action) {
    case "ban":
      return "bg-red-500/20 text-red-400 border border-red-500/40";
    case "kick":
      return "bg-orange-500/20 text-orange-400 border border-orange-500/40";
    case "timeout":
      return "bg-yellow-500/20 text-yellow-400 border border-yellow-500/40";
    case "warn":
      return "bg-amber-500/20 text-amber-400 border border-amber-500/40";
    case "unban":
      return "bg-teal-500/20 text-teal-400 border border-teal-500/40";
    case "clear":
      return "bg-blue-500/20 text-blue-400 border border-blue-500/40";
    default:
      return "bg-slate-700 text-slate-300 border border-slate-600";
  }
}

const PAGE_SIZE = 50;

export default function ModerationPage() {
  const [data, setData] = useState<ModResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [userIdInput, setUserIdInput] = useState("");
  const [debouncedUserId, setDebouncedUserId] = useState("");
  const [actionType, setActionType] = useState("");

  // Debounce user ID search by 400ms
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedUserId(userIdInput);
      setPage(1);
    }, 400);
    return () => clearTimeout(timer);
  }, [userIdInput]);

  // Reset to page 1 when action type filter changes
  useEffect(() => {
    setPage(1);
  }, [actionType]);

  // Fetch mod history whenever page/filter changes
  useEffect(() => {
    setData(null);
    setError(null);

    const params = new URLSearchParams({
      page: String(page),
      page_size: String(PAGE_SIZE),
    });
    const parsedId = parseInt(debouncedUserId, 10);
    if (!isNaN(parsedId) && debouncedUserId.trim() !== "") {
      params.set("target_id", String(parsedId));
    }
    if (actionType) params.set("action_type", actionType);

    apiFetch<ModResponse>(`/api/moderation?${params.toString()}`)
      .then(setData)
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : "Failed to load moderation history",
        );
      });
  }, [page, debouncedUserId, actionType]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div>
      {/* Header row */}
      <div className="flex items-center justify-between mb-6 gap-4 flex-wrap">
        <h1 className="text-xl font-semibold text-white">Moderation History</h1>
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Search by user ID…"
            value={userIdInput}
            onChange={(e) => setUserIdInput(e.target.value)}
            className="w-44 bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-purple-500"
          />
          <select
            value={actionType}
            onChange={(e) => setActionType(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white focus:outline-none focus:border-purple-500"
          >
            <option value="">All types</option>
            {ACTION_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="text-red-400 text-sm mb-4">
          Error loading moderation history: {error}
        </div>
      )}

      {/* Content */}
      {!data && !error ? (
        <div className="text-slate-400 text-sm">Loading...</div>
      ) : (
        <>
          {data && data.items.length === 0 ? (
            <p className="text-slate-500 text-sm">No moderation actions found.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {(data?.items ?? []).map((item) => (
                <div
                  key={item.id}
                  className="bg-slate-800 rounded-lg px-4 py-3 flex flex-col gap-1.5"
                >
                  {/* Top row: badge + target + moderator + timestamp */}
                  <div className="flex items-center justify-between gap-4 flex-wrap">
                    <div className="flex items-center gap-3 min-w-0">
                      <span
                        className={`text-xs font-semibold uppercase tracking-wide px-2 py-0.5 rounded ${actionBadgeClass(item.action_type)}`}
                      >
                        {item.action_type}
                      </span>
                      <span className="text-white text-sm font-medium">
                        {item.target_name ?? `User ${item.target_id}`}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-slate-400 text-xs">
                        by {item.moderator_name ?? item.moderator_id}
                      </span>
                      <span className="text-slate-500 text-xs">
                        {relativeTime(item.created_at)}
                      </span>
                    </div>
                  </div>
                  {/* Reason */}
                  {item.reason ? (
                    <p className="text-slate-400 text-sm italic">{item.reason}</p>
                  ) : (
                    <p className="text-slate-600 text-sm italic">
                      No reason provided
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Pagination */}
          {data && data.total > PAGE_SIZE && (
            <div className="flex items-center justify-center gap-4 mt-6">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className={`bg-purple-700 hover:bg-purple-600 text-white px-4 py-1.5 rounded-md text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                ← Prev
              </button>
              <span className="text-slate-400 text-sm">
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className={`bg-purple-700 hover:bg-purple-600 text-white px-4 py-1.5 rounded-md text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
