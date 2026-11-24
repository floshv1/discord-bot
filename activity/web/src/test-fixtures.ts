import type { GameState, Guess, Hint, HintKind, Solved } from "./types";

export const guessRow = (number: number, name: string): Guess => ({
  number,
  champion_id: name,
  name,
  cells: ["green", "yellow", "red", "green", "red", "green", "green", "red", "up"],
  clues: ["Mid", "Mage", "Distance", "Mana", "Demacia", "Femme", "AP", "Humain", "2010"],
});

export const hint = (over: Partial<Hint> = {}): Hint => ({ owned: false, price: 25, available: false, ...over });

export function gameState(
  over: Partial<Omit<GameState, "hints">> & { hints?: Partial<Record<HintKind, Hint>>; solved?: Solved | null } = {},
): GameState {
  return {
    day: "2026-11-24",
    guesses: [],
    solved: null,
    balance: 1000,
    live: [],
    ...over,
    hints: {
      spell: hint({ price: 25 }),
      splash: hint({ price: 50 }),
      letter: hint({ price: 100 }),
      ...over.hints,
    },
  };
}
