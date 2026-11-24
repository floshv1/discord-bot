import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

vi.mock("../api", () => ({ api: { ticket: vi.fn() } }));

import { api } from "../api";
import { WinCard } from "./WinCard";

const SOLVED = { name: "Lux", tries: 4, hints: 1, score: 5, streak: 3 };

test("nom, essais, score, série et splash avec ticket", async () => {
  vi.mocked(api.ticket).mockResolvedValue("tk");
  render(<WinCard solved={SOLVED} />);
  expect(screen.getByText("Lux")).toBeTruthy();
  expect(screen.getByText("4")).toBeTruthy();
  expect(screen.getByText("5")).toBeTruthy();
  expect(screen.getByText("🔥 3")).toBeTruthy();
  const img = await screen.findByRole("img");
  expect(api.ticket).toHaveBeenCalledWith("splash");
  expect(img.getAttribute("src")).toBe("/api/result/splash.png?ticket=tk");
});

test("splash en échec : un nouveau ticket puis un repli, la carte reste complète", async () => {
  vi.mocked(api.ticket).mockReset();
  vi.mocked(api.ticket).mockResolvedValueOnce("a").mockResolvedValueOnce("b");
  render(<WinCard solved={SOLVED} />);
  fireEvent.error(await screen.findByRole("img"));
  await waitFor(() => expect(screen.getByRole("img").getAttribute("src")).toContain("ticket=b"));
  fireEvent.error(screen.getByRole("img"));
  expect(await screen.findByText("Image indisponible pour le moment.")).toBeTruthy();
  expect(screen.getByText("Lux")).toBeTruthy();
});
