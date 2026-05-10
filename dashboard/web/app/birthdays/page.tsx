"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

interface Birthday {
  user_id: number;
  username: string;
  display_name: string | null;
  day: number;
  month: number;
  year: number;
  next_birthday: string;
  days_until: number;
  age: number;
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function daysLabel(delta: number): string {
  if (delta === 0) return "Today! 🎂";
  if (delta === 1) return "Tomorrow";
  return `In ${delta} days`;
}

export default function BirthdaysPage() {
  const [data, setData] = useState<Birthday[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<Birthday[]>("/api/birthdays")
      .then(setData)
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : "Failed to load birthdays",
        );
      });
  }, []);

  return (
    <div>
      <h1 className="text-xl font-semibold text-white mb-6">Birthdays</h1>

      {error && (
        <div className="text-red-400 text-sm mb-4">
          Error loading birthdays: {error}
        </div>
      )}

      {!data && !error ? (
        <div className="text-slate-400 text-sm">Loading...</div>
      ) : data && data.length === 0 ? (
        <p className="text-slate-500 text-sm">No birthdays registered yet.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {(data ?? []).map((b) => (
            <div
              key={b.user_id}
              className="bg-slate-800 rounded-lg px-4 py-3 flex items-center justify-between gap-4 flex-wrap"
            >
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-2xl">🎂</span>
                <div className="min-w-0">
                  <p className="text-white text-sm font-medium truncate">
                    {b.display_name ?? b.username}
                  </p>
                  <p className="text-slate-500 text-xs">
                    {String(b.day).padStart(2, "0")} {MONTHS[b.month - 1]}{" "}
                    {b.year}
                  </p>
                </div>
              </div>
              <div className="text-right shrink-0">
                <p
                  className={`text-sm font-medium ${
                    b.days_until === 0 ? "text-yellow-400" : "text-slate-300"
                  }`}
                >
                  {daysLabel(b.days_until)}
                </p>
                <p className="text-slate-500 text-xs">Turns {b.age}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
