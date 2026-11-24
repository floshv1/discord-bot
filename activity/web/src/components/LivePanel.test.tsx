import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { LivePanel } from "./LivePanel";
import type { LivePlayer } from "../types";

const p = (over: Partial<LivePlayer> = {}): LivePlayer => ({
  user_id: "1",
  name: "Zed",
  avatar: "https://cdn.discordapp.com/avatars/1/x.png",
  tries: 2,
  solved: false,
  last_cells: ["green", "yellow", "red", "green", "red", "green", "green", "red", "up"],
  online: true,
  ...over,
});

test("le panneau n'affiche jamais de nom de champion", () => {
  const leaky = { ...p(), champion: "Ahri" } as LivePlayer;
  const { container } = render(<LivePanel players={[leaky]} meId="9" status="live" phone={false} />);
  expect(container.textContent).not.toContain("Ahri");
  expect(container.querySelectorAll("img")).toHaveLength(0); // no CDN avatar: the CSP would block it
  expect(container.querySelectorAll(".sq")).toHaveLength(9);
});

test("pseudo, essais, ✅ et « toi »", () => {
  render(
    <LivePanel
      players={[p({ user_id: "1", name: "Zed", solved: true, tries: 1 }), p({ user_id: "2", name: "Lux", tries: 4 })]}
      meId="2"
      status="live"
      phone={false}
    />,
  );
  expect(screen.getByText("1 essai")).toBeTruthy();
  expect(screen.getByText("4 essais")).toBeTruthy();
  expect(screen.getAllByLabelText("a trouvé")).toHaveLength(1);
  expect(screen.getByText("toi")).toBeTruthy();
});

test("statut : en direct, mise à jour toutes les 5 s", () => {
  const { rerender } = render(<LivePanel players={[]} meId="1" status="live" phone={false} />);
  expect(screen.getByText("En direct")).toBeTruthy();
  rerender(<LivePanel players={[]} meId="1" status="polling" phone={false} />);
  expect(screen.getByText("Mise à jour toutes les 5 s")).toBeTruthy();
});

test("téléphone : replié par défaut, le bouton compte les joueurs en ligne", () => {
  render(
    <LivePanel
      players={[p({ user_id: "1" }), p({ user_id: "2", name: "Lux" }), p({ user_id: "3", name: "Off", online: false })]}
      meId="1"
      status="live"
      phone
    />,
  );
  const toggle = screen.getByRole("button", { name: /2 en ligne/ });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("Lux")).toBeNull();
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Lux")).toBeTruthy();
});

test("sans joueur : un message court", () => {
  render(<LivePanel players={[]} meId="1" status="live" phone={false} />);
  expect(screen.getByText(/Personne d'autre/)).toBeTruthy();
});

test("téléphone : le statut reste visible replié, et le bouton pointe vers le panneau", () => {
  render(<LivePanel players={[p()]} meId="1" status="polling" phone />);
  const toggle = screen.getByRole("button", { name: /1 en ligne/ });
  expect(toggle.textContent).toContain("Mise à jour toutes les 5 s");
  const id = toggle.getAttribute("aria-controls");
  expect(id).toBeTruthy();
  fireEvent.click(toggle);
  expect(document.getElementById(id as string)?.textContent).toContain("Zed");
});
