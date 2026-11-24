import { COLUMNS, type Cell, type Color, type Guess } from "./types";

const CLASS: Record<Color, string> = {
  green: "g",
  yellow: "y",
  red: "r",
  up: "r",
  down: "r",
  none: "n",
};

export const STATE_LABEL: Record<Color, string> = {
  green: "exact",
  yellow: "partiel",
  red: "différent",
  up: "plus récent",
  down: "plus ancien",
  none: "inconnu",
};

export const colorClass = (color: Color): string => CLASS[color];

/** Column name and state as text, for screen readers: colour is never the only signal. */
export const describeCell = (column: number, cell: Cell): string =>
  `${COLUMNS[column]} : ${cell.text}, ${STATE_LABEL[cell.color]}`;

/** Pairs each colour with its value; the year (last column) carries the arrow towards the answer. */
export function cellsOf(guess: Guess): Cell[] {
  return guess.cells.map((color, i) => {
    const value = guess.clues[i] ?? "?";
    const arrow = i === guess.cells.length - 1 ? (color === "up" ? " ↑" : color === "down" ? " ↓" : "") : "";
    return { color, text: `${value}${arrow}` };
  });
}
