"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

interface SuggestionItem {
  id: number;
  number: number;
  guild_id: number;
  author_id: number;
  type: "feature" | "improvement";
  content: string;
  status: "open" | "accepted" | "rejected" | "implemented";
  created_at: string;
  upvotes: number;
  downvotes: number;
}

interface SuggestionsResponse {
  items: SuggestionItem[];
  total: number;
  page: number;
  page_size: number;
}

const STATUS_OPTIONS = ["open", "accepted", "rejected", "implemented"] as const;
const TYPE_OPTIONS = ["feature", "improvement"] as const;

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case "open":
      return "bg-yellow-500/20 text-yellow-400 border border-yellow-500/40";
    case "accepted":
      return "bg-green-500/20 text-green-400 border border-green-500/40";
    case "rejected":
      return "bg-red-500/20 text-red-400 border border-red-500/40";
    case "implemented":
      return "bg-blue-500/20 text-blue-400 border border-blue-500/40";
    default:
      return "bg-slate-700 text-slate-300 border border-slate-600";
  }
}

function typeBadgeClass(type: string): string {
  switch (type) {
    case "feature":
      return "bg-purple-500/20 text-purple-400 border border-purple-500/40";
    case "improvement":
      return "bg-sky-500/20 text-sky-400 border border-sky-500/40";
    default:
      return "bg-slate-700 text-slate-300 border border-slate-600";
  }
}

const PAGE_SIZE = 20;

export default function SuggestionsPage() {
  const [data, setData] = useState<SuggestionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [mutateError, setMutateError] = useState<string | null>(null);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [statusFilter, typeFilter]);

  // Fetch suggestions
  useEffect(() => {
    setData(null);
    setError(null);

    const params = new URLSearchParams({
      page: String(page),
      page_size: String(PAGE_SIZE),
    });
    if (statusFilter) params.set("status", statusFilter);
    if (typeFilter) params.set("type", typeFilter);

    apiFetch<SuggestionsResponse>(`/api/suggestions?${params.toString()}`)
      .then(setData)
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : "Failed to load suggestions",
        );
      });
  }, [page, statusFilter, typeFilter]);

  async function updateStatus(
    id: number,
    newStatus: "accepted" | "rejected" | "implemented",
  ) {
    if (loading) return;
    setLoading(true);
    setMutateError(null);
    try {
      await apiFetch(`/api/suggestions/${id}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status: newStatus }),
      });
      // Refresh current page
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(PAGE_SIZE),
      });
      if (statusFilter) params.set("status", statusFilter);
      if (typeFilter) params.set("type", typeFilter);
      const refreshed = await apiFetch<SuggestionsResponse>(
        `/api/suggestions?${params.toString()}`,
      );
      setData(refreshed);
    } catch (err: unknown) {
      setMutateError(
        err instanceof Error ? err.message : "Failed to update suggestion",
      );
    } finally {
      setLoading(false);
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div>
      {/* Header row */}
      <div className="flex items-center justify-between mb-6 gap-4 flex-wrap">
        <h1 className="text-xl font-semibold text-white">Suggestions</h1>
        <div className="flex items-center gap-3">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white focus:outline-none focus:border-purple-500"
          >
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white focus:outline-none focus:border-purple-500"
          >
            <option value="">All types</option>
            {TYPE_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Mutation error */}
      {mutateError && (
        <div className="text-red-400 text-sm mb-4">Error: {mutateError}</div>
      )}

      {/* Fetch error */}
      {error && (
        <div className="text-red-400 text-sm mb-4">
          Error loading suggestions: {error}
        </div>
      )}

      {/* Content */}
      {!data && !error ? (
        <div className="text-slate-400 text-sm">Loading...</div>
      ) : (
        <>
          {data && data.items.length === 0 ? (
            <p className="text-slate-500 text-sm">No suggestions found.</p>
          ) : (
            <div>
              {(data?.items ?? []).map((item) => {
                const truncated =
                  item.content.length > 80
                    ? item.content.slice(0, 80) + "…"
                    : item.content;

                return (
                  <div
                    key={item.id}
                    className="bg-slate-800 rounded-xl p-4 mb-3"
                  >
                    {/* Top row: number + content + status badge */}
                    <div className="flex items-start justify-between gap-3 mb-1.5">
                      <span className="font-medium text-white text-sm">
                        #{item.number} — {truncated}
                      </span>
                      <span
                        className={`text-xs px-2 py-0.5 rounded font-semibold shrink-0 ${statusBadgeClass(item.status)}`}
                      >
                        {item.status}
                      </span>
                    </div>

                    {/* Second row: type badge + author + votes + time */}
                    <div className="flex items-center gap-3 flex-wrap text-xs text-slate-400 mb-2">
                      <span
                        className={`px-2 py-0.5 rounded font-semibold ${typeBadgeClass(item.type)}`}
                      >
                        {item.type}
                      </span>
                      <span>by {item.author_id}</span>
                      <span>
                        👍 {item.upvotes} 👎 {item.downvotes}
                      </span>
                      <span className="text-slate-500">
                        {relativeTime(item.created_at)}
                      </span>
                    </div>

                    {/* Action buttons — only for open suggestions */}
                    {item.status === "open" && (
                      <div className="flex items-center gap-2 flex-wrap">
                        <button
                          onClick={() => updateStatus(item.id, "accepted")}
                          disabled={loading}
                          className="text-xs px-3 py-1 rounded border border-green-500/60 text-green-400 hover:bg-green-500/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          Accept
                        </button>
                        <button
                          onClick={() => updateStatus(item.id, "rejected")}
                          disabled={loading}
                          className="text-xs px-3 py-1 rounded border border-red-500/60 text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          Reject
                        </button>
                        <button
                          onClick={() => updateStatus(item.id, "implemented")}
                          disabled={loading}
                          className="text-xs px-3 py-1 rounded border border-blue-500/60 text-blue-400 hover:bg-blue-500/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          Implemented
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Pagination */}
          {data && data.total > PAGE_SIZE && (
            <div className="flex items-center justify-center gap-4 mt-6">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="bg-purple-700 hover:bg-purple-600 text-white px-4 py-1.5 rounded-md text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                ← Prev
              </button>
              <span className="text-slate-400 text-sm">
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="bg-purple-700 hover:bg-purple-600 text-white px-4 py-1.5 rounded-md text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
