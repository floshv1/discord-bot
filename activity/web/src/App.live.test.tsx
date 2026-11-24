import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("./api", () => ({
  ApiError: class extends Error {},
  api: {
    authenticate: vi.fn(),
    state: vi.fn(),
    champions: vi.fn(),
    guess: vi.fn(),
    buyHint: vi.fn(),
    ticket: vi.fn(),
  },
}));
vi.mock("./discord", () => ({ insideDiscord: vi.fn() }));

import { App } from "./App";
import { api } from "./api";
import { insideDiscord } from "./discord";
import { gameState, guessRow, hint } from "./test-fixtures";
import type { LivePlayer } from "./types";

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
  send(m: unknown) {
    this.sent.push(m);
  }
  sent: unknown[] = [];
}

const live = (over: Partial<LivePlayer>): LivePlayer => ({
  user_id: "2",
  name: "Lux",
  avatar: null,
  tries: 1,
  solved: false,
  last_cells: ["green", "red", "red", "red", "red", "red", "red", "red", "red"],
  online: true,
  ...over,
});

const settle = () => act(() => vi.advanceTimersByTimeAsync(0));

beforeEach(() => {
  FakeSocket.all = [];
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.mocked(insideDiscord).mockReturnValue(true);
  for (const fn of Object.values(api)) vi.mocked(fn).mockReset();
  vi.mocked(api.authenticate).mockResolvedValue("1");
  vi.mocked(api.state).mockResolvedValue(gameState({ live: [live({ user_id: "1", name: "Moi" })] }));
  vi.mocked(api.champions).mockResolvedValue([{ id: "Ahri", name: "Ahri" }]);
  vi.mocked(api.ticket).mockResolvedValue("tk");
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function open() {
  render(<App />);
  await screen.findByRole("combobox");
  await settle();
  const socket = FakeSocket.all[0];
  act(() => socket.onopen?.());
  return socket;
}

test("un message player met le panneau à jour en direct", async () => {
  const socket = await open();
  expect(screen.getByText("En direct")).toBeTruthy();
  expect(screen.getByText("toi")).toBeTruthy();
  act(() => socket.onmessage?.({ data: JSON.stringify({ type: "player", player: live({ user_id: "2", name: "Lux" }) }) }));
  expect(await screen.findByText("Lux")).toBeTruthy();
  expect(socket.sent).toEqual([]); // the client sends nothing
});

test("après l'échec du WebSocket, repli sur /api/state toutes les 5 s", async () => {
  const socket = await open();
  vi.mocked(api.state).mockClear();
  vi.mocked(api.state).mockResolvedValue(gameState({ live: [live({ user_id: "3", name: "Poll" })] }));
  act(() => socket.onclose?.());
  expect(screen.getByText("Mise à jour toutes les 5 s")).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(4900));
  expect(api.state).not.toHaveBeenCalled();
  await act(() => vi.advanceTimersByTimeAsync(200));
  expect(api.state).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Poll")).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(api.state).toHaveBeenCalledTimes(2);
  // the socket comes back: one refresh, then no more polling
  await act(() => vi.advanceTimersByTimeAsync(1000));
  const back = FakeSocket.all[FakeSocket.all.length - 1];
  vi.mocked(api.state).mockClear();
  act(() => back.onopen?.());
  await settle();
  expect(api.state).toHaveBeenCalledTimes(1);
  expect(screen.getByText("En direct")).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(20_000));
  expect(api.state).toHaveBeenCalledTimes(1);
});

test('{type:"day"} recharge la partie et remonte les images à ticket', async () => {
  const socket = await open();
  vi.mocked(api.state).mockClear();
  vi.mocked(api.state).mockResolvedValue(gameState({ day: "2026-11-25", guesses: [], live: [] }));
  act(() => socket.onmessage?.({ data: JSON.stringify({ type: "day" }) }));
  await waitFor(() => expect(api.state).toHaveBeenCalledTimes(1));
  expect(await screen.findByText(/25\/11/)).toBeTruthy();
});

test("{type:day} annule la minuterie de la carte de victoire", async () => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q }));
  const socket = await open();
  const won = gameState({
    guesses: [guessRow(1, "Ahri")],
    solved: { name: "Ahri", tries: 1, hints: 0, score: 1, streak: 1 },
  });
  vi.mocked(api.guess).mockResolvedValue({ status: "ok", state: won });
  const box = screen.getByRole("combobox");
  fireEvent.change(box, { target: { value: "Ahri" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(2)); // the reply is in
  expect(screen.queryByLabelText("Victoire")).toBeNull(); // still revealing
  vi.mocked(api.state).mockResolvedValue(gameState({ day: "2026-11-25" }));
  act(() => socket.onmessage?.({ data: JSON.stringify({ type: "day" }) }));
  expect(await screen.findByText(/25\/11/)).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(3000));
  expect(screen.queryByLabelText("Victoire")).toBeNull(); // the new day is not solved
});

test("le socket est fermé au démontage", async () => {
  const { unmount } = render(<App />);
  await screen.findByRole("combobox");
  await settle();
  const socket = FakeSocket.all[0];
  unmount();
  expect(socket.closed).toBe(true);
});

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

const WON = gameState({
  guesses: [guessRow(1, "Ahri")],
  solved: { name: "Ahri", tries: 1, hints: 0, score: 1, streak: 1 },
});

test("un rafraîchissement parti avant un essai mais arrivé après ne l'efface pas", async () => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q })); // reduced motion: win card at once
  const socket = await open();
  const slow = deferred<ReturnType<typeof gameState>>();
  vi.mocked(api.state).mockReturnValueOnce(slow.promise);
  act(() => socket.onmessage?.({ data: JSON.stringify({ type: "day" }) })); // refresh starts, stays pending
  vi.mocked(api.guess).mockResolvedValue({ status: "ok", state: WON });
  const box = screen.getByRole("combobox");
  fireEvent.change(box, { target: { value: "Ahri" } });
  fireEvent.keyDown(box, { key: "Enter" });
  expect(await screen.findByLabelText("Victoire")).toBeTruthy();
  await act(async () => slow.resolve(gameState({ guesses: [], solved: null })));
  expect(screen.getByLabelText("Victoire")).toBeTruthy();
  expect(screen.getAllByRole("row")).toHaveLength(2);
});

test("un rafraîchissement en retard n'écrase pas un joueur reçu entre-temps", async () => {
  const socket = await open();
  const slow = deferred<ReturnType<typeof gameState>>();
  vi.mocked(api.state).mockReturnValueOnce(slow.promise);
  act(() => socket.onmessage?.({ data: JSON.stringify({ type: "day" }) }));
  act(() => socket.onmessage?.({ data: JSON.stringify({ type: "player", player: live({ user_id: "7", name: "Neuf" }) }) }));
  expect(await screen.findByText("Neuf")).toBeTruthy();
  await act(async () => slow.resolve(gameState({ live: [live({ user_id: "1", name: "Moi" })] })));
  expect(screen.getByText("Neuf")).toBeTruthy();
  expect(screen.getByText("Moi")).toBeTruthy();
});

test("un message day remonte les images à ticket des indices", async () => {
  vi.mocked(api.state).mockResolvedValue(
    gameState({ guesses: [guessRow(1, "A")], hints: { spell: hint({ owned: true, text: "Sort" }) } }),
  );
  const socket = await open();
  const spellTickets = () => vi.mocked(api.ticket).mock.calls.filter((c) => c[0] === "hint:spell").length;
  await waitFor(() => expect(spellTickets()).toBe(1));
  act(() => socket.onmessage?.({ data: JSON.stringify({ type: "day" }) }));
  await waitFor(() => expect(spellTickets()).toBe(2));
});

test("une présence inconnue déclenche un seul rafraîchissement, et le joueur apparaît", async () => {
  const socket = await open();
  const calls = vi.mocked(api.state).mock.calls.length;
  vi.mocked(api.state).mockResolvedValue(
    gameState({ live: [live({ user_id: "1", name: "Moi" }), live({ user_id: "9", name: "Arrivé", tries: 0, last_cells: [] })] }),
  );
  const presence = JSON.stringify({ type: "presence", user_id: "9", online: true });
  act(() => socket.onmessage?.({ data: presence }));
  act(() => socket.onmessage?.({ data: presence }));
  expect(vi.mocked(api.state).mock.calls.length).toBe(calls); // debounced, not immediate
  await act(() => vi.advanceTimersByTimeAsync(400));
  expect(vi.mocked(api.state).mock.calls.length).toBe(calls + 1);
  expect(await screen.findByText("Arrivé")).toBeTruthy();
});

test("une présence suivie de son message player ne rafraîchit pas", async () => {
  const socket = await open();
  const calls = vi.mocked(api.state).mock.calls.length;
  act(() => socket.onmessage?.({ data: JSON.stringify({ type: "presence", user_id: "9", online: true }) }));
  act(() =>
    socket.onmessage?.({ data: JSON.stringify({ type: "player", player: live({ user_id: "9", name: "Arrivé", tries: 0, last_cells: [] }) }) }),
  );
  await act(() => vi.advanceTimersByTimeAsync(400));
  expect(vi.mocked(api.state).mock.calls.length).toBe(calls);
  expect(await screen.findByText("Arrivé")).toBeTruthy();
});
