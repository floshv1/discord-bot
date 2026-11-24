import { describe, expect, test } from "vitest";
import { normalise, searchChampions } from "./search";

const LIST = [
  { id: "Ahri", name: "Ahri" },
  { id: "Kaisa", name: "Kai'Sa" },
  { id: "Akali", name: "Akali" },
  { id: "Lux", name: "Lux" },
  { id: "Zac", name: "Zac" },
  { id: "Rakan", name: "Rakan" },
];

describe("normalise", () => {
  test("ignore accents, casse et apostrophes", () => expect(normalise("Kaï'Sa")).toBe(normalise("kaisa")));
});

describe("searchChampions", () => {
  test("les noms qui commencent par la saisie passent d'abord", () => {
    const names = searchChampions(LIST, "ak", new Set()).map((r) => r.name);
    expect(names).toEqual(["Akali", "Rakan"]);
  });

  test("un champion déjà tenté est marqué, pas retiré", () => {
    const hit = searchChampions(LIST, "ahri", new Set(["Ahri"]));
    expect(hit).toHaveLength(1);
    expect(hit[0].tried).toBe(true);
  });

  test("la saisie accentuée retrouve le nom avec apostrophe", () => {
    expect(searchChampions(LIST, "KAÏS", new Set())[0].id).toBe("Kaisa");
  });

  test("au plus huit résultats par défaut, et rien pour une saisie vide", () => {
    const many = Array.from({ length: 20 }, (_, i) => ({ id: `A${i}`, name: `A${i}` }));
    expect(searchChampions(many, "a", new Set())).toHaveLength(8);
    expect(searchChampions(LIST, "  ", new Set())).toEqual([]);
  });
});
