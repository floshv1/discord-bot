import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { Guess } from "../types";
import { Grid } from "./Grid";

const guess = (number: number, name: string, year: string, last: "up" | "down" = "up"): Guess => ({
  number,
  champion_id: name,
  name,
  cells: ["yellow", "green", "green", "green", "red", "green", "green", "red", last],
  clues: ["Mid/Support", "Mage", "Distance", "Mana", "Demacia", "Femme", "AP", "Humain", year],
});

test("la grille affiche le libellé et une flèche pour l'année", () => {
  render(<Grid guesses={[guess(1, "Lux", "2010")]} />);
  expect(screen.getByText("Mid/Support")).toBeTruthy();
  expect(screen.getByText("2010 ↑")).toBeTruthy();
  expect(screen.getAllByRole("columnheader")).toHaveLength(10);
});

test("la flèche descend quand le champion cherché est plus ancien", () => {
  render(<Grid guesses={[guess(1, "Lux", "2020", "down")]} />);
  expect(screen.getByText("2020 ↓")).toBeTruthy();
});

test("le plus récent est en haut", () => {
  render(<Grid guesses={[guess(1, "Ahri", "2011"), guess(2, "Lux", "2010"), guess(3, "Zac", "2013")]} />);
  const rows = screen.getAllByRole("row").slice(1); // skip the header
  expect(rows.map((r) => r.getAttribute("data-number"))).toEqual(["3", "2", "1"]);
});
