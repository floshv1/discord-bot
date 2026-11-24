import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("./api", () => ({ api: { ticket: vi.fn() } }));

import { api } from "./api";
import { applyMessage, connectLive, needsRefresh, sortPlayers } from "./live";
import type { LivePlayer } from "./types";

const p = (over: Partial<LivePlayer> = {}): LivePlayer => ({
  user_id: "1",
  name: "Zed",
  avatar: null,
  tries: 0,
  solved: false,
  last_cells: [],
  online: true,
  ...over,
});

test("en ligne d'abord, puis par ordre de progression", () => {
  const sorted = sortPlayers([
    p({ user_id: "1", name: "Off", online: false, solved: true, tries: 1 }),
    p({ user_id: "2", name: "Lent", tries: 9 }),
    p({ user_id: "3", name: "Trouvé tard", solved: true, tries: 7 }),
    p({ user_id: "4", name: "Trouvé tôt", solved: true, tries: 2 }),
    p({ user_id: "5", name: "Alpha", tries: 3 }),
    p({ user_id: "6", name: "Beta", tries: 3 }),
  ]);
  expect(sorted.map((x) => x.name)).toEqual(["Trouvé tôt", "Trouvé tard", "Alpha", "Beta", "Lent", "Off"]);
});

test("un message player remplace la ligne du joueur", () => {
  const before = [p({ user_id: "1", tries: 1 }), p({ user_id: "2" })];
  const after = applyMessage(before, { type: "player", player: p({ user_id: "1", tries: 4 }) });
  expect(after.find((x) => x.user_id === "1")?.tries).toBe(4);
  expect(after).toHaveLength(2);
  expect(before[0].tries).toBe(1); // not mutated
  expect(applyMessage(before, { type: "player", player: p({ user_id: "9" }) })).toHaveLength(3);
});

test("presence met à jour l'état en ligne, day ne touche pas la liste", () => {
  const before = [p({ user_id: "1" })];
  expect(applyMessage(before, { type: "presence", user_id: "1", online: false })[0].online).toBe(false);
  expect(applyMessage(before, { type: "presence", user_id: "7", online: true })).toEqual(before);
  expect(applyMessage(before, { type: "day" })).toBe(before);
});

test("une présence inconnue demande un rafraîchissement, pas les autres", () => {
  const before = [p({ user_id: "1" })];
  expect(needsRefresh(before, { type: "presence", user_id: "7", online: true })).toBe(true);
  expect(needsRefresh(before, { type: "presence", user_id: "7", online: false })).toBe(false);
  expect(needsRefresh(before, { type: "presence", user_id: "1", online: true })).toBe(false);
  expect(needsRefresh(before, { type: "day" })).toBe(false);
  expect(needsRefresh(before, { type: "player", player: p({ user_id: "7" }) })).toBe(false);
});

class FakeSocket {
  static all: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeSocket.all.push(this);
  }
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  FakeSocket.all = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  let n = 0;
  vi.mocked(api.ticket).mockImplementation(async () => `tk${++n}`);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("connexion : ticket neuf, hôte relatif, messages transmis", async () => {
  const seen: unknown[] = [];
  connectLive((m) => seen.push(m), vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  const s = FakeSocket.all[0];
  expect(s.url).toBe(`ws://${location.host}/ws?ticket=tk1`);
  s.onmessage?.({ data: JSON.stringify({ type: "day" }) });
  s.onmessage?.({ data: "pas du json" });
  s.onmessage?.({ data: JSON.stringify({ type: "inconnu" }) });
  expect(seen).toEqual([{ type: "day" }]);
});

test("après l'échec du WebSocket : repli, reconnexion avec backoff, retour", async () => {
  const fallback = vi.fn();
  connectLive(vi.fn(), fallback);
  await vi.advanceTimersByTimeAsync(0);
  FakeSocket.all[0].onopen?.();
  FakeSocket.all[0].onclose?.();
  expect(fallback).toHaveBeenLastCalledWith(true);
  await vi.advanceTimersByTimeAsync(999);
  expect(FakeSocket.all).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(FakeSocket.all).toHaveLength(2);
  expect(FakeSocket.all[1].url).toContain("ticket=tk2"); // a fresh ticket per attempt
  FakeSocket.all[1].onclose?.(); // fails again: 2 s
  await vi.advanceTimersByTimeAsync(1999);
  expect(FakeSocket.all).toHaveLength(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(FakeSocket.all).toHaveLength(3);
  FakeSocket.all[2].onopen?.();
  expect(fallback).toHaveBeenLastCalledWith(false);
});

test("le délai plafonne à 30 s", async () => {
  connectLive(vi.fn(), vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  for (let i = 0; i < 8; i++) {
    FakeSocket.all[FakeSocket.all.length - 1].onclose?.();
    await vi.advanceTimersByTimeAsync(30_000);
  }
  const count = FakeSocket.all.length;
  FakeSocket.all[count - 1].onclose?.();
  await vi.advanceTimersByTimeAsync(30_000);
  expect(FakeSocket.all).toHaveLength(count + 1);
});

test("un ticket refusé compte comme un échec et on réessaie", async () => {
  const fallback = vi.fn();
  vi.mocked(api.ticket).mockRejectedValueOnce(new Error("x"));
  connectLive(vi.fn(), fallback);
  await vi.advanceTimersByTimeAsync(0);
  expect(fallback).toHaveBeenCalledWith(true);
  expect(FakeSocket.all).toHaveLength(0);
  await vi.advanceTimersByTimeAsync(1000);
  expect(FakeSocket.all).toHaveLength(1);
});

test("fermer coupe le socket et arrête les reconnexions", async () => {
  const stop = connectLive(vi.fn(), vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  const s = FakeSocket.all[0];
  stop();
  expect(s.closed).toBe(true);
  s.onclose?.();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(FakeSocket.all).toHaveLength(1);
});

test("close 4008 après l'ouverture : prochaine tentative à 30 s au moins", async () => {
  connectLive(vi.fn(), vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  FakeSocket.all[0].onopen?.();
  (FakeSocket.all[0].onclose as unknown as (e: { code: number }) => void)({ code: 4008 });
  await vi.advanceTimersByTimeAsync(29_999);
  expect(FakeSocket.all).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(5_001);
  expect(FakeSocket.all).toHaveLength(2);
});

test("ouvrir puis se faire fermer à répétition : le délai double toujours", async () => {
  connectLive(vi.fn(), vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  for (const delay of [1000, 2000, 4000, 8000]) {
    const before = FakeSocket.all.length;
    const s = FakeSocket.all[before - 1];
    s.onopen?.();
    await vi.advanceTimersByTimeAsync(500); // open well under 10 s
    s.onclose?.();
    await vi.advanceTimersByTimeAsync(delay - 1);
    expect(FakeSocket.all).toHaveLength(before);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeSocket.all).toHaveLength(before + 1);
  }
});

test("un socket resté ouvert 10 s remet le délai à 1 s", async () => {
  connectLive(vi.fn(), vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  FakeSocket.all[0].onclose?.(); // 1 s, attempt now 1
  await vi.advanceTimersByTimeAsync(1000);
  FakeSocket.all[1].onopen?.();
  await vi.advanceTimersByTimeAsync(10_000);
  FakeSocket.all[1].onclose?.();
  await vi.advanceTimersByTimeAsync(1000);
  expect(FakeSocket.all).toHaveLength(3);
});

test("un message player mal formé est ignoré", async () => {
  const seen: unknown[] = [];
  connectLive((m) => seen.push(m), vi.fn());
  await vi.advanceTimersByTimeAsync(0);
  const s = FakeSocket.all[0];
  const good = { user_id: "1", name: "A", avatar: null, tries: 1, solved: false, last_cells: ["green"], online: true };
  for (const bad of [
    { ...good, last_cells: undefined },
    { ...good, last_cells: ["pink"] },
    { ...good, user_id: 1 },
    { ...good, tries: "2" },
    { ...good, avatar: 5 },
    { ...good, online: "yes" },
  ]) {
    s.onmessage?.({ data: JSON.stringify({ type: "player", player: bad }) });
  }
  s.onmessage?.({ data: JSON.stringify({ type: "presence", user_id: 3, online: true }) });
  expect(seen).toEqual([]);
  s.onmessage?.({ data: JSON.stringify({ type: "player", player: good }) });
  expect(seen).toHaveLength(1);
});
