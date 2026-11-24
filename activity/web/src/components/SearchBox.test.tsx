import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { SearchBox } from "./SearchBox";

const CHAMPS = [
  { id: "Ahri", name: "Ahri" },
  { id: "Akali", name: "Akali" },
  { id: "Akshan", name: "Akshan" },
];

const input = () => screen.getByRole("combobox");
const key = (k: string) => fireEvent.keyDown(input(), { key: k });
const type = (text: string) => fireEvent.change(input(), { target: { value: text } });
const active = () => screen.getAllByRole("option").find((o) => o.getAttribute("aria-selected") === "true")?.textContent;

test("les flèches déplacent l'option active et Entrée la soumet", () => {
  const onSubmit = vi.fn();
  render(<SearchBox champions={CHAMPS} tried={new Set()} onSubmit={onSubmit} />);
  type("a");
  expect(active()).toContain("Ahri");
  key("ArrowDown");
  expect(active()).toContain("Akali");
  key("ArrowDown");
  key("ArrowUp");
  expect(active()).toContain("Akali");
  key("Enter");
  expect(onSubmit).toHaveBeenCalledWith("Akali");
});

test("au plus huit résultats", () => {
  const many = Array.from({ length: 20 }, (_, i) => ({ id: `B${i}`, name: `B${i}` }));
  render(<SearchBox champions={many} tried={new Set()} onSubmit={vi.fn()} />);
  type("b");
  expect(screen.getAllByRole("option")).toHaveLength(8);
});

test("un champion tenté est affiché mais ne se soumet pas", () => {
  const onSubmit = vi.fn();
  render(<SearchBox champions={CHAMPS} tried={new Set(["Ahri"])} onSubmit={onSubmit} />);
  type("ahri");
  const option = screen.getByRole("option");
  expect(option.getAttribute("aria-disabled")).toBe("true");
  expect(option.textContent).toContain("déjà tenté");
  key("Enter");
  fireEvent.mouseDown(option);
  expect(onSubmit).not.toHaveBeenCalled();
});

test("les flèches sautent un champion tenté", () => {
  render(<SearchBox champions={CHAMPS} tried={new Set(["Akali"])} onSubmit={vi.fn()} />);
  type("a");
  key("ArrowDown");
  expect(active()).toContain("Akshan");
});

test("occupé : la saisie reste focalisable et rien n'est envoyé", () => {
  const onSubmit = vi.fn();
  render(<SearchBox champions={CHAMPS} tried={new Set()} busy onSubmit={onSubmit} />);
  type("ahri");
  key("Enter");
  expect(onSubmit).not.toHaveBeenCalled();
  expect((input() as HTMLInputElement).disabled).toBe(false);
});
