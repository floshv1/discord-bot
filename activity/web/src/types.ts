// All ids are strings: a Discord snowflake does not fit a JS number.

export type Color = "green" | "yellow" | "red" | "up" | "down" | "none";

export interface Cell {
  color: Color;
  text: string;
}

export interface Guess {
  number: number;
  champion_id: string;
  name: string;
  cells: Color[];
  clues: string[];
}

export type HintKind = "spell" | "splash" | "letter";

export interface Hint {
  owned: boolean;
  price: number;
  available: boolean;
  text?: string;
}

export interface Solved {
  name: string;
  tries: number;
  hints: number;
  score: number;
  streak: number;
}

export interface LivePlayer {
  user_id: string;
  name: string;
  avatar: string | null;
  tries: number;
  solved: boolean;
  last_cells: Color[];
  online: boolean;
}

export interface GameState {
  day: string;
  guesses: Guess[];
  solved: Solved | null;
  hints: Record<HintKind, Hint>;
  balance: number;
  live: LivePlayer[];
}

export interface Champion {
  id: string;
  name: string;
}

export type GuessStatus = "ok" | "duplicate" | "solved_already" | "unknown";
export type BuyStatus = "bought" | "owned" | "locked" | "poor" | "unavailable";

export interface GuessReply {
  status: GuessStatus;
  state: GameState;
}

export interface HintReply {
  status: BuyStatus;
  balance: number;
  state: GameState;
}

export const COLUMNS = ["Poste", "Classe", "Portée", "Ress.", "Région", "Genre", "Dégâts", "Espèce", "Année"] as const;
