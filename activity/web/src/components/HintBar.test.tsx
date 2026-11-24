import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("../api", () => ({ api: { ticket: vi.fn() } }));

import { api } from "../api";
import { gameState, hint } from "../test-fixtures";
import { HintBar } from "./HintBar";

beforeEach(() => vi.mocked(api.ticket).mockReset());

test("verrouillé, disponible et acheté, avec le prix", () => {
  const { hints } = gameState({
    hints: { spell: hint({ owned: true, text: "✨ Un de ses sorts :" }), splash: hint({ price: 50, available: true }) },
  });
  vi.mocked(api.ticket).mockResolvedValue("t");
  render(<HintBar hints={hints} tries={3} onBuy={vi.fn()} />);
  const buttons = screen.getAllByRole("button");
  expect(buttons[0].textContent).toContain("acheté");
  expect((buttons[1] as HTMLButtonElement).disabled).toBe(false);
  expect(buttons[1].textContent).toContain("50 🪙");
  expect((buttons[2] as HTMLButtonElement).disabled).toBe(true);
  expect(buttons[2].textContent).toContain("100 🪙");
  expect(buttons[2].textContent).toContain("🔒");
});

test("un indice disponible déclenche l'achat", () => {
  const onBuy = vi.fn();
  const { hints } = gameState({ hints: { spell: hint({ available: true }) } });
  render(<HintBar hints={hints} tries={3} onBuy={onBuy} />);
  fireEvent.click(screen.getAllByRole("button")[0]);
  expect(onBuy).toHaveBeenCalledWith("spell");
});

test("l'indice lettre affiche son texte sans balisage", () => {
  const { hints } = gameState({ hints: { letter: hint({ owned: true, text: "🔤 Son nom commence par **L**." }) } });
  render(<HintBar hints={hints} tries={5} onBuy={vi.fn()} />);
  expect(screen.getByText("🔤 Son nom commence par L.")).toBeTruthy();
});

test("un indice image demande un ticket et affiche l'image", async () => {
  vi.mocked(api.ticket).mockResolvedValue("tk 1");
  const { hints } = gameState({ hints: { spell: hint({ owned: true, text: "✨ Un de ses sorts :" }) } });
  render(<HintBar hints={hints} tries={3} onBuy={vi.fn()} />);
  const img = (await screen.findByRole("img")) as HTMLImageElement;
  expect(api.ticket).toHaveBeenCalledWith("hint:spell");
  expect(img.getAttribute("src")).toBe("/api/hints/spell.png?ticket=tk%201");
});

test("une image en échec : un seul nouveau ticket, puis le repli français", async () => {
  vi.mocked(api.ticket).mockResolvedValueOnce("old").mockResolvedValueOnce("new");
  const { hints } = gameState({ hints: { splash: hint({ owned: true, text: "🖼️ Un bout de son splash art :" }) } });
  render(<HintBar hints={hints} tries={3} onBuy={vi.fn()} />);
  fireEvent.error(await screen.findByRole("img"));
  await waitFor(() => expect(screen.getByRole("img").getAttribute("src")).toContain("ticket=new"));
  expect(api.ticket).toHaveBeenCalledTimes(2);
  fireEvent.error(screen.getByRole("img"));
  expect(await screen.findByText("Image indisponible pour le moment.")).toBeTruthy();
  expect(api.ticket).toHaveBeenCalledTimes(2);
});
