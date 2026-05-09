"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

interface LogItem {
  id: number;
  event_type: string;
  actor_id: number | null;
  target_id: number | null;
  channel_id: number | null;
  details: Record<string, unknown> | null;
  created_at: string;
}

interface LogsResponse {
  items: LogItem[];
  total: number;
  page: number;
  page_size: number;
}

const EVENT_TYPE_OPTIONS = [
  "message_sent",
  "message_deleted",
  "message_edited",
  "member_joined",
  "member_left",
  "member_banned",
  "member_unbanned",
  "voice_joined",
  "voice_left",
  "slash_command",
  "role_added",
  "role_removed",
];

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function eventTypeColor(eventType: string): string {
  if (eventType.startsWith("message_")) return "text-orange-400";
  if (eventType === "member_joined") return "text-green-400";
  if (eventType === "member_left" || eventType === "member_banned")
    return "text-red-400";
  if (eventType.startsWith("member_")) return "text-green-400";
  if (eventType.startsWith("voice_")) return "text-blue-400";
  if (eventType === "slash_command") return "text-indigo-400";
  if (eventType.startsWith("role_") || eventType.startsWith("nickname_"))
    return "text-purple-400";
  if (eventType.startsWith("channel_") || eventType.startsWith("thread_"))
    return "text-sky-400";
  if (eventType.startsWith("invite_")) return "text-slate-400";
  return "text-slate-300";
}

function eventTypeBorderColor(eventType: string): string {
  if (eventType.startsWith("message_")) return "border-orange-400";
  if (eventType === "member_joined") return "border-green-400";
  if (eventType === "member_left" || eventType === "member_banned")
    return "border-red-400";
  if (eventType.startsWith("member_")) return "border-green-400";
  if (eventType.startsWith("voice_")) return "border-blue-400";
  if (eventType === "slash_command") return "border-indigo-400";
  if (eventType.startsWith("role_") || eventType.startsWith("nickname_"))
    return "border-purple-400";
  if (eventType.startsWith("channel_") || eventType.startsWith("thread_"))
    return "border-sky-400";
  if (eventType.startsWith("invite_")) return "border-slate-400";
  return "border-slate-300";
}

function buildDetailsSummary(item: LogItem): string {
  const parts: string[] = [];
  if (item.actor_id != null) parts.push(`by ${item.actor_id}`);
  if (item.channel_id != null) parts.push(`in #${item.channel_id}`);
  if (item.details != null) {
    const detailStr = JSON.stringify(item.details);
    const truncated =
      detailStr.length > 60 ? detailStr.slice(0, 60) + "…" : detailStr;
    parts.push(truncated);
  }
  return parts.join(" · ");
}

const PAGE_SIZE = 50;

export default function LogsPage() {
  const [data, setData] = useState<LogsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [eventType, setEventType] = useState("");

  // Debounce search input by 400ms
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 400);
    return () => clearTimeout(timer);
  }, [search]);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [eventType]);

  // Fetch logs whenever page/filter/search changes
  useEffect(() => {
    setData(null);
    setError(null);

    const params = new URLSearchParams({
      page: String(page),
      page_size: String(PAGE_SIZE),
    });
    if (debouncedSearch) params.set("search", debouncedSearch);
    if (eventType) params.set("event_type", eventType);

    apiFetch<LogsResponse>(`/api/logs?${params.toString()}`)
      .then(setData)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load logs");
      });
  }, [page, debouncedSearch, eventType]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div>
      {/* Header row */}
      <div className="flex items-center justify-between mb-6 gap-4 flex-wrap">
        <h1 className="text-xl font-semibold text-white">Audit Logs</h1>
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Search events…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-48 bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-purple-500"
          />
          <select
            value={eventType}
            onChange={(e) => setEventType(e.target.value)}
            className="bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white focus:outline-none focus:border-purple-500"
          >
            <option value="">All types</option>
            {EVENT_TYPE_OPTIONS.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Content */}
      {error && (
        <div className="text-red-400 text-sm mb-4">
          Error loading logs: {error}
        </div>
      )}

      {!data && !error ? (
        <div className="text-slate-400 text-sm">Loading...</div>
      ) : (
        <>
          {data && data.items.length === 0 ? (
            <p className="text-slate-500 text-sm">No log entries found.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {(data?.items ?? []).map((item) => {
                const color = eventTypeColor(item.event_type);
                const borderColor = eventTypeBorderColor(item.event_type);
                const summary = buildDetailsSummary(item);

                return (
                  <div
                    key={item.id}
                    className={`bg-slate-800 rounded-lg px-4 py-2.5 flex gap-3 items-start border-l-[3px] ${borderColor}`}
                  >
                    <div className="flex flex-col gap-0.5 flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-4">
                        <span
                          className={`text-xs font-semibold uppercase tracking-wide min-w-[120px] ${color}`}
                        >
                          {item.event_type}
                        </span>
                        <span className="text-slate-400 text-xs shrink-0">
                          {relativeTime(item.created_at)}
                        </span>
                      </div>
                      {summary && (
                        <p className="text-slate-400 text-xs truncate">
                          {summary}
                        </p>
                      )}
                    </div>
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
                className={`bg-purple-700 hover:bg-purple-600 text-white px-4 py-1.5 rounded-md text-sm transition-colors ${
                  page <= 1 ? "opacity-50 cursor-not-allowed" : ""
                }`}
              >
                ← Prev
              </button>
              <span className="text-slate-400 text-sm">
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className={`bg-purple-700 hover:bg-purple-600 text-white px-4 py-1.5 rounded-md text-sm transition-colors ${
                  page >= totalPages ? "opacity-50 cursor-not-allowed" : ""
                }`}
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
