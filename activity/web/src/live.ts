import { api } from "./api";
import type { LivePlayer } from "./types";

export type LiveMessage =
  | { type: "player"; player: LivePlayer }
  | { type: "presence"; user_id: string; online: boolean }
  | { type: "day" };

/** Online first, then solved before unsolved, then fewer tries, then name. */
export function sortPlayers(players: LivePlayer[]): LivePlayer[] {
  return [...players].sort(
    (a, b) =>
      Number(b.online) - Number(a.online) ||
      Number(b.solved) - Number(a.solved) ||
      a.tries - b.tries ||
      a.name.localeCompare(b.name, "fr"),
  );
}

/** Pure: returns a new list for "player" and "presence", the same list for anything else. */
export function applyMessage(players: LivePlayer[], msg: LiveMessage): LivePlayer[] {
  if (msg.type === "player") {
    const known = players.some((p) => p.user_id === msg.player.user_id);
    return known ? players.map((p) => (p.user_id === msg.player.user_id ? msg.player : p)) : [...players, msg.player];
  }
  if (msg.type === "presence") {
    return players.map((p) => (p.user_id === msg.user_id ? { ...p, online: msg.online } : p));
  }
  return players;
}

/** A member came online whom the panel has never heard of: only the server knows their name. */
export function needsRefresh(players: LivePlayer[], msg: LiveMessage): boolean {
  return msg.type === "presence" && msg.online && !players.some((p) => p.user_id === msg.user_id);
}

const COLORS = new Set(["green", "yellow", "red", "up", "down", "none"]);

function validPlayer(p: unknown): p is LivePlayer {
  if (typeof p !== "object" || p === null) return false;
  const v = p as Record<string, unknown>;
  return (
    typeof v.user_id === "string" &&
    typeof v.name === "string" &&
    typeof v.tries === "number" &&
    typeof v.solved === "boolean" &&
    typeof v.online === "boolean" &&
    (v.avatar === null || typeof v.avatar === "string") &&
    Array.isArray(v.last_cells) &&
    v.last_cells.every((c) => typeof c === "string" && COLORS.has(c))
  );
}

function parse(data: unknown): LiveMessage | null {
  if (typeof data !== "string") return null;
  try {
    const m = JSON.parse(data) as Record<string, unknown> | null;
    if (typeof m !== "object" || m === null) return null;
    if (m.type === "day") return { type: "day" };
    if (m.type === "player" && validPlayer(m.player)) return { type: "player", player: m.player };
    if (m.type === "presence" && typeof m.user_id === "string" && typeof m.online === "boolean") {
      return { type: "presence", user_id: m.user_id, online: m.online };
    }
  } catch {
    // not JSON: ignored
  }
  return null;
}

const BASE_MS = 1000;
const MAX_MS = 30_000;
const STABLE_MS = 10_000; // a socket must stay open this long (or speak) before the backoff resets
const CLOSE_TOO_MANY = 4008; // the server's "too many sockets", sent after the handshake

/** 1 s doubling to 30 s, with +-20 % jitter, never above the cap. */
export function backoff(attempt: number): number {
  const base = Math.min(MAX_MS, BASE_MS * 2 ** attempt);
  return Math.min(MAX_MS, base * (0.8 + Math.random() * 0.4));
}

/**
 * Keeps one WebSocket open, reconnecting with a fresh ticket each time. `onFallback(true)` fires when the
 * socket is lost (the caller should poll), `onFallback(false)` when it is back after a loss. `onOpen`
 * fires on every open. Returns a function that closes everything.
 */
export function connectLive(
  onMessage: (msg: LiveMessage) => void,
  onFallback: (down: boolean) => void,
  onOpen?: () => void,
): () => void {
  let closed = false;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let down = false;

  const setDown = (value: boolean) => {
    if (value === down) return;
    down = value;
    onFallback(value);
  };

  let stable: ReturnType<typeof setTimeout> | null = null;
  const clearStable = () => {
    if (stable) clearTimeout(stable);
    stable = null;
  };

  const retry = (code?: number) => {
    setDown(true);
    const delay = code === CLOSE_TOO_MANY ? MAX_MS + Math.random() * 5000 : backoff(attempt++);
    timer = setTimeout(open, delay);
  };

  async function open() {
    timer = null;
    let ticket: string;
    try {
      ticket = await api.ticket("ws");
    } catch {
      if (!closed) retry();
      return;
    }
    if (closed) return;
    const scheme = location.protocol === "http:" ? "ws:" : "wss:";
    const ws = new WebSocket(`${scheme}//${location.host}/ws?ticket=${encodeURIComponent(ticket)}`);
    socket = ws;
    ws.onopen = () => {
      if (closed || socket !== ws) return;
      clearStable();
      stable = setTimeout(() => {
        attempt = 0;
      }, STABLE_MS);
      setDown(false);
      onOpen?.();
    };
    ws.onmessage = (event) => {
      if (closed || socket !== ws) return;
      const msg = parse(event.data);
      if (msg) {
        attempt = 0;
        onMessage(msg);
      }
    };
    ws.onerror = () => ws.close();
    ws.onclose = (event?: { code?: number }) => {
      if (closed || socket !== ws) return;
      socket = null;
      clearStable();
      retry(event?.code);
    };
  }

  void open();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    clearStable();
    const ws = socket;
    socket = null;
    ws?.close();
  };
}
