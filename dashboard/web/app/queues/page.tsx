"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

interface QueueMember {
  user_id: number;
  joined_at: string;
  in_lane: boolean;
  cant_attend: boolean;
}

interface Queue {
  id: number;
  guild_id: number;
  channel_id: number;
  preset_id: number;
  creator_user_id: number | null;
  status: "open" | "filled";
  created_at: string;
  filled_at: string | null;
  start_time: string | null;
  message_id: number | null;
  game_name: string;
  player_count: number;
  members: QueueMember[];
}

interface Preset {
  id: number;
  guild_id: number;
  name: string;
  player_count: number;
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

function activeMembers(members: QueueMember[]): QueueMember[] {
  return members.filter((m) => !m.in_lane && !m.cant_attend);
}

function waitingMembers(members: QueueMember[]): QueueMember[] {
  return members.filter((m) => m.in_lane);
}

export default function QueuesPage() {
  const [queues, setQueues] = useState<Queue[] | null>(null);
  const [queuesError, setQueuesError] = useState<string | null>(null);
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [presetsError, setPresetsError] = useState<string | null>(null);

  // Mutation state
  const [mutating, setMutating] = useState(false);
  const [mutateError, setMutateError] = useState<string | null>(null);

  // Add preset form state
  const [newPresetName, setNewPresetName] = useState("");
  const [newPresetCount, setNewPresetCount] = useState(2);
  const [addingPreset, setAddingPreset] = useState(false);
  const [addPresetError, setAddPresetError] = useState<string | null>(null);

  function fetchQueues() {
    setQueuesError(null);
    apiFetch<Queue[]>("/api/queues")
      .then(setQueues)
      .catch((err: unknown) => {
        setQueuesError(
          err instanceof Error ? err.message : "Failed to load queues",
        );
      });
  }

  function fetchPresets() {
    setPresetsError(null);
    apiFetch<Preset[]>("/api/presets")
      .then(setPresets)
      .catch((err: unknown) => {
        setPresetsError(
          err instanceof Error ? err.message : "Failed to load presets",
        );
      });
  }

  useEffect(() => {
    fetchQueues();
    fetchPresets();
  }, []);

  async function cancelQueue(id: number) {
    if (mutating) return;
    setMutating(true);
    setMutateError(null);
    try {
      await apiFetch(`/api/queues/${id}`, { method: "DELETE" });
      fetchQueues();
    } catch (err: unknown) {
      setMutateError(
        err instanceof Error ? err.message : "Failed to cancel queue",
      );
    } finally {
      setMutating(false);
    }
  }

  async function deletePreset(id: number) {
    if (mutating) return;
    setMutating(true);
    setMutateError(null);
    try {
      await apiFetch(`/api/presets/${id}`, { method: "DELETE" });
      fetchPresets();
    } catch (err: unknown) {
      setMutateError(
        err instanceof Error ? err.message : "Failed to delete preset",
      );
    } finally {
      setMutating(false);
    }
  }

  async function addPreset(e: React.FormEvent) {
    e.preventDefault();
    if (addingPreset || !newPresetName.trim()) return;
    setAddingPreset(true);
    setAddPresetError(null);
    try {
      await apiFetch("/api/presets", {
        method: "POST",
        body: JSON.stringify({
          name: newPresetName.trim(),
          player_count: newPresetCount,
        }),
      });
      setNewPresetName("");
      setNewPresetCount(2);
      fetchPresets();
    } catch (err: unknown) {
      setAddPresetError(
        err instanceof Error ? err.message : "Failed to add preset",
      );
    } finally {
      setAddingPreset(false);
    }
  }

  return (
    <div>
      <h1 className="text-xl font-semibold text-white mb-6">Game Queues</h1>

      {/* Global mutation error */}
      {mutateError && (
        <div className="text-red-400 text-sm mb-4">Error: {mutateError}</div>
      )}

      {/* Active Queues */}
      <section className="mb-8">
        <h2 className="text-base font-semibold text-slate-300 mb-3">
          Active Queues
        </h2>

        {queuesError && (
          <div className="text-red-400 text-sm mb-4">
            Error loading queues: {queuesError}
          </div>
        )}

        {!queues && !queuesError ? (
          <div className="text-slate-400 text-sm">Loading...</div>
        ) : queues && queues.length === 0 ? (
          <p className="text-slate-500 text-sm">No active queues.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {(queues ?? []).map((queue) => {
              const active = activeMembers(queue.members);
              const waiting = waitingMembers(queue.members);
              const displayMembers = active.slice(0, 5);
              const overflow = active.length - displayMembers.length;

              return (
                <div key={queue.id} className="bg-slate-800 rounded-xl p-4">
                  {/* Header */}
                  <div className="flex items-center justify-between mb-2 gap-2">
                    <span className="font-semibold text-white truncate">
                      {queue.game_name}
                    </span>
                    <div className="flex items-center gap-2 shrink-0">
                      <span
                        className={`text-xs px-2 py-0.5 rounded font-semibold ${
                          queue.status === "open"
                            ? "bg-green-500/20 text-green-400 border border-green-500/40"
                            : "bg-blue-500/20 text-blue-400 border border-blue-500/40"
                        }`}
                      >
                        {queue.status}
                      </span>
                      <span className="text-slate-400 text-xs">
                        {active.length}/{queue.player_count} players
                      </span>
                    </div>
                  </div>

                  {/* Creator */}
                  <p className="text-slate-400 text-xs mb-2">
                    {queue.creator_user_id != null
                      ? `Created by ${queue.creator_user_id}`
                      : "Created by unknown"}{" "}
                    · {relativeTime(queue.created_at)}
                  </p>

                  {/* Active members */}
                  {displayMembers.length > 0 && (
                    <div className="flex flex-col gap-0.5 mb-1">
                      {displayMembers.map((m) => (
                        <span
                          key={m.user_id}
                          className="text-slate-300 text-xs"
                        >
                          User {m.user_id}
                        </span>
                      ))}
                      {overflow > 0 && (
                        <span className="text-slate-500 text-xs">
                          …and {overflow} more
                        </span>
                      )}
                    </div>
                  )}

                  {/* Waiting */}
                  {waiting.length > 0 && (
                    <p className="text-slate-500 text-xs mb-2">
                      Waiting: {waiting.length}
                    </p>
                  )}

                  {/* Cancel button */}
                  <button
                    onClick={() => cancelQueue(queue.id)}
                    disabled={mutating}
                    className="mt-2 text-xs px-3 py-1 rounded border border-red-500/60 text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Cancel Queue
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Game Presets */}
      <section>
        <h2 className="text-base font-semibold text-slate-300 mb-3">
          Game Presets
        </h2>

        {presetsError && (
          <div className="text-red-400 text-sm mb-3">
            Error loading presets: {presetsError}
          </div>
        )}

        {!presets && !presetsError ? (
          <div className="text-slate-400 text-sm mb-3">Loading presets...</div>
        ) : (
          <div className="flex flex-wrap gap-2 mb-4">
            {(presets ?? []).length === 0 && (
              <span className="text-slate-500 text-sm">No presets yet.</span>
            )}
            {(presets ?? []).map((preset) => (
              <div
                key={preset.id}
                className="bg-slate-700 rounded-lg px-3 py-2 text-sm flex items-center gap-2"
              >
                <span className="text-white">
                  {preset.name} · {preset.player_count} players
                </span>
                <button
                  onClick={() => deletePreset(preset.id)}
                  disabled={mutating}
                  className="text-slate-400 hover:text-red-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed leading-none"
                  aria-label={`Delete ${preset.name}`}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Add preset form */}
        <form onSubmit={addPreset} className="flex items-center gap-2 flex-wrap">
          <input
            type="text"
            placeholder="Preset name"
            value={newPresetName}
            onChange={(e) => setNewPresetName(e.target.value)}
            className="w-40 bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-purple-500"
          />
          <input
            type="number"
            min={2}
            max={100}
            value={newPresetCount}
            onChange={(e) => setNewPresetCount(Number(e.target.value))}
            className="w-20 bg-slate-800 border border-slate-700 rounded-md px-3 py-1.5 text-sm text-white focus:outline-none focus:border-purple-500"
          />
          <button
            type="submit"
            disabled={addingPreset || !newPresetName.trim()}
            className="bg-purple-700 hover:bg-purple-600 text-white px-4 py-1.5 rounded-md text-sm disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {addingPreset ? "Adding…" : "Add"}
          </button>
        </form>
        {addPresetError && (
          <p className="text-red-400 text-xs mt-1">{addPresetError}</p>
        )}
      </section>
    </div>
  );
}
