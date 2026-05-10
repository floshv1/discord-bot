"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

interface LeaderboardEntry {
  rank: number;
  user_id: number;
  user_name: string | null;
  duration: string;
  total_seconds: number;
}

interface VoiceData {
  weekly: LeaderboardEntry[];
  alltime: LeaderboardEntry[];
}

function rankLabel(rank: number): string {
  if (rank === 1) return "🥇";
  if (rank === 2) return "🥈";
  if (rank === 3) return "🥉";
  return `#${rank}`;
}

function LeaderboardTable({
  title,
  entries,
  accent,
}: {
  title: string;
  entries: LeaderboardEntry[];
  accent: string;
}) {
  return (
    <div className="flex-1 min-w-[260px]">
      <h2 className={`text-base font-semibold mb-3 ${accent}`}>{title}</h2>
      {entries.length === 0 ? (
        <p className="text-slate-500 text-sm">No data yet.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {entries.map((entry) => (
            <div
              key={entry.user_id}
              className="bg-slate-800 rounded-lg px-4 py-3 flex items-center justify-between gap-4"
            >
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-lg w-8 text-center shrink-0">
                  {rankLabel(entry.rank)}
                </span>
                <span className="text-white text-sm font-medium truncate">
                  {entry.user_name ?? `User ${entry.user_id}`}
                </span>
              </div>
              <span className="text-slate-300 text-sm font-mono shrink-0">
                {entry.duration}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function VoicePage() {
  const [data, setData] = useState<VoiceData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<VoiceData>("/api/voice")
      .then(setData)
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : "Failed to load voice data",
        );
      });
  }, []);

  return (
    <div>
      <h1 className="text-xl font-semibold text-white mb-6">
        Voice Leaderboard
      </h1>

      {error && (
        <div className="text-red-400 text-sm mb-4">
          Error loading voice data: {error}
        </div>
      )}

      {!data && !error ? (
        <div className="text-slate-400 text-sm">Loading...</div>
      ) : data ? (
        <div className="flex gap-6 flex-wrap">
          <LeaderboardTable
            title="🎙️ Last 7 Days"
            entries={data.weekly}
            accent="text-blue-400"
          />
          <LeaderboardTable
            title="🏆 All Time"
            entries={data.alltime}
            accent="text-yellow-400"
          />
        </div>
      ) : null}
    </div>
  );
}
