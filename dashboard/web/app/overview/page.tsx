"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

interface RecentActivity {
  event_type: string;
  actor_id: number | null;
  actor_name?: string | null;
  channel_id: number | null;
  details: Record<string, unknown> | null;
  created_at: string;
}

interface OverviewData {
  total_mod_actions: number;
  open_queues: number;
  open_suggestions: number;
  audit_events_today: number;
  recent_activity: RecentActivity[];
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatEventType(eventType: string): string {
  return eventType
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function eventTypeColor(eventType: string): string {
  if (eventType.startsWith("message_")) return "text-orange-400";
  if (eventType === "member_joined") return "text-green-400";
  if (
    eventType === "member_left" ||
    eventType === "member_banned"
  )
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

interface StatCardProps {
  label: string;
  value: number;
  subtitle?: string;
}

function StatCard({ label, value, subtitle }: StatCardProps) {
  return (
    <div className="bg-slate-800 rounded-xl p-4">
      <p className="text-slate-400 text-xs mb-1">{label}</p>
      <p className="text-white text-3xl font-bold">{value.toLocaleString()}</p>
      {subtitle && <p className="text-slate-400 text-xs mt-1">{subtitle}</p>}
    </div>
  );
}

export default function OverviewPage() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<OverviewData>("/api/overview")
      .then(setData)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load data");
      });
  }, []);

  if (error) {
    return (
      <div className="text-red-400 text-sm">
        Error loading overview: {error}
      </div>
    );
  }

  if (!data) {
    return <div className="text-slate-400 text-sm">Loading...</div>;
  }

  const recentActivity = data.recent_activity.slice(0, 10);

  return (
    <div>
      <h1 className="text-xl font-semibold text-white mb-6">
        Server Overview
      </h1>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard label="Total Mod Actions" value={data.total_mod_actions} />
        <StatCard label="Open Queues" value={data.open_queues} />
        <StatCard label="Open Suggestions" value={data.open_suggestions} />
        <StatCard
          label="Audit Events Today"
          value={data.audit_events_today}
          subtitle="last 24h"
        />
      </div>

      {/* Recent Activity */}
      <p className="text-sm text-slate-400 mb-3">Recent Activity</p>

      {recentActivity.length === 0 ? (
        <p className="text-slate-500 text-sm">No recent activity.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {recentActivity.map((event, idx) => {
            const color = eventTypeColor(event.event_type);
            const detailsSummary =
              event.details != null
                ? JSON.stringify(event.details).slice(0, 80)
                : null;

            return (
              <div
                key={idx}
                className="bg-slate-800 rounded-lg px-4 py-2.5 flex items-start justify-between gap-4"
              >
                <div className="flex flex-col gap-0.5 min-w-0">
                  <span className={`text-sm font-medium ${color}`}>
                    {formatEventType(event.event_type)}
                  </span>
                  {event.actor_id != null && (
                    <span className="text-slate-500 text-xs">
                      by {event.actor_name ?? `User ${event.actor_id}`}
                    </span>
                  )}
                  {detailsSummary && (
                    <span className="text-slate-400 text-xs truncate">
                      {detailsSummary}
                    </span>
                  )}
                </div>
                <span className="text-slate-400 text-xs shrink-0 mt-0.5">
                  {relativeTime(event.created_at)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
