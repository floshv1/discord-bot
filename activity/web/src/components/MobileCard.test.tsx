import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { MobileCard } from "./MobileCard";
import { guessRow } from "../test-fixtures";

test("le dernier essai en 3×3 avec libellés, les anciens en pastilles dépliables", () => {
  const { container } = render(<MobileCard guesses={[guessRow(1, "Ahri"), guessRow(2, "Lux")]} freshNumber={null} />);
  expect(container.querySelectorAll(".mcell")).toHaveLength(9);
  expect(screen.getByText("Poste")).toBeTruthy();
  const older = screen.getByRole("button", { name: /Ahri/ });
  expect(older.getAttribute("aria-expanded")).toBe("false");
  expect(container.querySelectorAll(".mrow .sq")).toHaveLength(9);
  fireEvent.click(older);
  expect(older.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getAllByText("Demacia")).toHaveLength(2); // latest card + the expanded row
});

test("aucun essai : rien à afficher", () => {
  const { container } = render(<MobileCard guesses={[]} freshNumber={null} />);
  expect(container.textContent).toBe("");
});
