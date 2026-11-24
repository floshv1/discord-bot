import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("./api", () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number) {
      super(`HTTP ${status}`);
      this.status = status;
    }
  }
  return {
    ApiError,
    api: {
      authenticate: vi.fn(),
      state: vi.fn(),
      champions: vi.fn(),
      guess: vi.fn(),
      buyHint: vi.fn(),
      ticket: vi.fn(),
    },
  };
});
vi.mock("./discord", () => ({ insideDiscord: vi.fn() }));

import { App } from "./App";
import { api } from "./api";
import { insideDiscord } from "./discord";
import { gameState, guessRow, hint } from "./test-fixtures";

const CHAMPS = [
  { id: "Ahri", name: "Ahri" },
  { id: "Lux", name: "Lux" },
];

function stubMotion(reduced: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: reduced, media: query }));
}

beforeEach(() => {
  vi.mocked(insideDiscord).mockReturnValue(true);
  for (const fn of Object.values(api)) vi.mocked(fn).mockReset();
  vi.mocked(api.authenticate).mockResolvedValue("1");
  vi.mocked(api.state).mockResolvedValue(gameState());
  vi.mocked(api.champions).mockResolvedValue(CHAMPS);
  vi.mocked(api.ticket).mockResolvedValue("tk");
  stubMotion(false);
});

const search = async () => (await screen.findByRole("combobox")) as HTMLInputElement;

async function guessAs(name: string) {
  const box = await search();
  box.focus();
  fireEvent.change(box, { target: { value: name } });
  fireEvent.keyDown(box, { key: "Enter" });
  return box;
}

test("hors de Discord : un message en français, aucun appel", () => {
  vi.mocked(insideDiscord).mockReturnValue(false);
  render(<App />);
  expect(screen.getByText(/Ouvre le jeu depuis Discord/)).toBeTruthy();
  expect(api.authenticate).not.toHaveBeenCalled();
});

test("après un essai, la saisie garde le focus", async () => {
  let release!: () => void;
  vi.mocked(api.guess).mockImplementation(
    () =>
      new Promise((resolve) => {
        release = () => resolve({ status: "ok", state: gameState({ guesses: [guessRow(1, "Ahri")] }) });
      }),
  );
  render(<App />);
  const box = await guessAs("ahri");
  expect(document.activeElement).toBe(box); // still focused while the request runs
  expect(box.disabled).toBe(false); // a disabled input loses focus in a real browser
  release();
  await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(2));
  expect(document.activeElement).toBe(box);
});

test.each([
  ["unknown", "Champion inconnu : vérifie l'orthographe."],
  ["duplicate", "Tu as déjà tenté ce champion."],
  ["solved_already", "Tu as déjà trouvé aujourd'hui."],
] as const)("la réponse %s affiche le bon message", async (status, message) => {
  vi.mocked(api.guess).mockResolvedValue({ status, state: gameState() });
  render(<App />);
  await guessAs("zzz");
  expect(await screen.findByText(message)).toBeTruthy();
  expect(screen.queryAllByRole("row")).toHaveLength(1); // header only: nothing recorded
});

test.each([
  ["poor", "Tu n'as pas assez de FloshCoins."],
  ["locked", "Cet indice n'est pas encore disponible."],
  ["unavailable", "Indice indisponible pour le moment, réessaie plus tard."],
] as const)("achat d'indice %s : message français", async (status, message) => {
  vi.mocked(api.state).mockResolvedValue(
    gameState({ guesses: [guessRow(1, "A"), guessRow(2, "B"), guessRow(3, "C")], hints: { spell: hint({ available: true }) } }),
  );
  vi.mocked(api.buyHint).mockResolvedValue({ status, balance: 10, state: gameState() });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /Sort/ }));
  expect(await screen.findByText(message)).toBeTruthy();
});

test("liste des champions en échec : la partie reste jouable, une seule relance", async () => {
  vi.mocked(api.champions).mockRejectedValue(new Error("boom"));
  render(<App />);
  expect(await screen.findByText(/liste des champions est indisponible/)).toBeTruthy();
  expect(api.champions).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("table")).toBeTruthy();
  vi.mocked(api.guess).mockResolvedValue({ status: "ok", state: gameState({ guesses: [guessRow(1, "Lux")] }) });
  await guessAs("Lux");
  expect(api.guess).toHaveBeenCalledWith("Lux");
});

const WON = gameState({
  guesses: [guessRow(1, "Lux")],
  solved: { name: "Lux", tries: 1, hints: 0, score: 1, streak: 2 },
});

test("la carte de victoire attend la fin de la révélation", async () => {
  vi.mocked(api.guess).mockResolvedValue({ status: "ok", state: WON });
  render(<App />);
  await guessAs("Lux");
  await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(2));
  expect(screen.queryByLabelText("Victoire")).toBeNull();
  expect(await screen.findByLabelText("Victoire", {}, { timeout: 3500 })).toBeTruthy();
}, 6000);

test("mouvement réduit : la carte de victoire est immédiate", async () => {
  stubMotion(true);
  vi.mocked(api.guess).mockResolvedValue({ status: "ok", state: WON });
  render(<App />);
  await guessAs("Lux");
  expect(await screen.findByLabelText("Victoire")).toBeTruthy();
});

test("page chargée déjà résolue : la carte est immédiate", async () => {
  vi.mocked(api.state).mockResolvedValue(WON);
  render(<App />);
  expect(await screen.findByLabelText("Victoire")).toBeTruthy();
});
