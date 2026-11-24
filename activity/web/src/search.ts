import type { Champion } from "./types";

/** "Kaï'Sa", "kaisa" and "KAÏSA" are the same search (same rule as the bot's `catalog.normalise`). */
export function normalise(text: string): string {
  return Array.from(text.normalize("NFKD").toLowerCase())
    .filter((ch) => /[\p{L}\p{N}]/u.test(ch))
    .join("");
}

export interface SearchResult extends Champion {
  tried: boolean;
}

/** Names starting with the input first, then names containing it. Tried ones are marked, not removed. */
export function searchChampions(
  list: Champion[],
  text: string,
  tried: ReadonlySet<string>,
  limit = 8,
): SearchResult[] {
  const needle = normalise(text);
  if (!needle) return [];
  const starts: SearchResult[] = [];
  const contains: SearchResult[] = [];
  for (const champion of list) {
    const name = normalise(champion.name);
    const hit = { ...champion, tried: tried.has(champion.id) };
    if (name.startsWith(needle)) starts.push(hit);
    else if (name.includes(needle)) contains.push(hit);
  }
  return [...starts, ...contains].slice(0, limit);
}
