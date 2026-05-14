"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";

interface Track {
  title: string;
  author: string | null;
  uri: string;
  length_ms: number;
  artwork: string | null;
  requester_id: number | null;
}

interface QueueEntry {
  position: number;
  title: string;
  author: string | null;
  uri: string | null;
  length_ms: number;
  requester_id: number | null;
}

interface MusicState {
  playing: true;
  track: Track;
  is_paused: boolean;
  volume: number;
  autoplay_enabled: boolean;
  position_ms: number;
  updated_at: string;
  queue: QueueEntry[];
}

type MusicResponse = { playing: false } | MusicState;

function fmtMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0)
    return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export default function MusicPage() {
  const [state, setState] = useState<MusicResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [position, setPosition] = useState(0);
  const [localVolume, setLocalVolume] = useState(100);

  const updatedAtRef = useRef(0);
  const positionAtFetchRef = useRef(0);
  const isPausedRef = useRef(false);
  const trackLengthRef = useRef(0);
  const isDraggingRef = useRef(false);

  const fetchState = useCallback(async () => {
    try {
      const data = await apiFetch<MusicResponse>("/api/music");
      setState(data);
      setError(null);
      if (data.playing) {
        positionAtFetchRef.current = data.position_ms;
        updatedAtRef.current = new Date(data.updated_at).getTime();
        isPausedRef.current = data.is_paused;
        setPosition(data.position_ms);
        trackLengthRef.current = data.track.length_ms;
        if (!isDraggingRef.current) setLocalVolume(data.volume);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    }
  }, []);

  useEffect(() => {
    fetchState();
    const id = setInterval(fetchState, 3000);
    return () => clearInterval(id);
  }, [fetchState]);

  useEffect(() => {
    const id = setInterval(() => {
      if (!isPausedRef.current && updatedAtRef.current > 0) {
        const raw = positionAtFetchRef.current + (Date.now() - updatedAtRef.current);
        setPosition(trackLengthRef.current > 0 ? Math.min(raw, trackLengthRef.current) : raw);
      }
    }, 1000);
    return () => clearInterval(id);
  }, []);

  async function sendCommand(method: string, path: string, body?: object) {
    try {
      await apiFetch(`/api/music${path}`, {
        method,
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      await fetchState();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Command failed");
    }
  }

  if (!state && !error) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-white mb-6">Music</h1>
        <div className="text-slate-400 text-sm">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-white mb-6">Music</h1>
        <div className="text-red-400 text-sm">Error: {error}</div>
      </div>
    );
  }

  if (!state?.playing) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-white mb-6">Music</h1>
        <div className="bg-slate-800 rounded-xl p-10 text-center">
          <div className="text-5xl mb-4">🎵</div>
          <p className="text-slate-300 font-medium">Nothing is playing right now.</p>
          <p className="text-slate-500 text-sm mt-2">
            Use{" "}
            <code className="bg-slate-700 px-1.5 py-0.5 rounded text-slate-300">/play</code>{" "}
            in Discord to start the music.
          </p>
        </div>
      </div>
    );
  }

  const { track, is_paused, autoplay_enabled, queue } = state;
  const lengthMs = Math.max(track.length_ms, 1);
  const clampedPos = Math.min(position, lengthMs);
  const progressPct = (clampedPos / lengthMs) * 100;

  return (
    <div>
      <h1 className="text-xl font-semibold text-white mb-6">Music</h1>

      {/* Now Playing */}
      <div className="bg-slate-800 rounded-xl overflow-hidden mb-4">
        <div className="flex gap-5 items-center p-5 border-b border-slate-700">
          {track.artwork ? (
            <img
              src={track.artwork}
              alt=""
              className="w-20 h-20 rounded-lg object-cover shrink-0"
            />
          ) : (
            <div className="w-20 h-20 rounded-lg bg-slate-700 flex items-center justify-center text-3xl shrink-0">
              🎵
            </div>
          )}

          <div className="flex-1 min-w-0">
            <a
              href={track.uri}
              target="_blank"
              rel="noopener noreferrer"
              className="text-white font-semibold text-lg hover:text-purple-400 transition-colors truncate block"
            >
              {track.title}
            </a>
            <div className="text-slate-400 text-sm mt-0.5">{track.author ?? "—"}</div>
            <div className="mt-3">
              <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-purple-500 rounded-full"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
              <div className="flex justify-between text-slate-500 text-xs mt-1">
                <span>{fmtMs(clampedPos)}</span>
                <span>{fmtMs(lengthMs)}</span>
              </div>
            </div>
          </div>

          {/* Controls */}
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => sendCommand("POST", "/pause")}
              className="w-12 h-12 rounded-full bg-purple-700 hover:bg-purple-600 flex items-center justify-center text-white text-xl transition-colors"
              title={is_paused ? "Resume" : "Pause"}
              aria-label={is_paused ? "Resume" : "Pause"}
            >
              {is_paused ? "▶" : "⏸"}
            </button>
            <button
              onClick={() => sendCommand("POST", "/skip")}
              className="w-10 h-10 rounded-lg bg-slate-700 hover:bg-slate-600 flex items-center justify-center text-white transition-colors"
              title="Skip"
              aria-label="Skip"
            >
              ⏭
            </button>
            <button
              onClick={() => sendCommand("POST", "/stop")}
              className="w-10 h-10 rounded-lg bg-slate-700 hover:bg-red-900/60 flex items-center justify-center text-white transition-colors"
              title="Stop"
              aria-label="Stop"
            >
              ⏹
            </button>
          </div>
        </div>

        {/* Status bar */}
        <div className="flex flex-wrap gap-4 px-5 py-2.5 text-xs">
          <span className={is_paused ? "text-yellow-400" : "text-green-400"}>
            {is_paused ? "⏸ Paused" : "▶ Playing"}
          </span>
          <span className="text-slate-500">
            Autoplay {autoplay_enabled ? "ON" : "OFF"}
          </span>
          <div className="flex items-center gap-2 text-slate-400">
            <span>🔊</span>
            <input
              type="range"
              min={0}
              max={100}
              value={localVolume}
              onChange={(e) => setLocalVolume(parseInt(e.target.value, 10))}
              onPointerDown={() => { isDraggingRef.current = true; }}
              onPointerUp={(e) => {
                isDraggingRef.current = false;
                sendCommand("PUT", "/volume", {
                  volume: parseInt((e.target as HTMLInputElement).value, 10),
                });
              }}
              className="w-24 accent-purple-500 cursor-pointer"
            />
            <span className="w-8 text-right">{localVolume}%</span>
          </div>
          <span className="text-slate-500">{queue.length} in queue</span>
        </div>
      </div>

      {/* Queue */}
      {queue.length > 0 && (
        <div className="bg-slate-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-700 text-xs text-slate-400 uppercase tracking-wider font-medium">
            Up Next
          </div>
          <div className="divide-y divide-slate-700/50">
            {queue.map((item) => (
              <div key={item.position} className="flex items-center gap-3 px-5 py-3">
                <span className="text-slate-600 text-sm w-5 text-center shrink-0">
                  {item.position}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-slate-200 text-sm truncate">{item.title}</div>
                  {item.author && (
                    <div className="text-slate-500 text-xs">{item.author}</div>
                  )}
                </div>
                <span className="text-slate-500 text-xs font-mono shrink-0">
                  {fmtMs(item.length_ms)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
