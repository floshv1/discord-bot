import { TicketImage } from "./TicketImage";
import type { GameState, HintKind } from "../types";

const KINDS: { kind: HintKind; label: string; icon: string }[] = [
  { kind: "spell", label: "Sort", icon: "✨" },
  { kind: "splash", label: "Splash", icon: "🖼️" },
  { kind: "letter", label: "Lettre", icon: "🔤" },
];

interface Props {
  hints: GameState["hints"];
  tries: number;
  busy?: boolean;
  onBuy: (kind: HintKind) => void;
}

function lockedReason(kind: HintKind, tries: number, hints: GameState["hints"]): string {
  const order = KINDS.map((k) => k.kind);
  const previous = order[order.indexOf(kind) - 1];
  if (tries < 3) return "Disponible après 3 essais";
  if (previous && !hints[previous].owned) return "Achète d'abord l'indice précédent";
  return "Indisponible";
}

export function HintBar({ hints, tries, busy = false, onBuy }: Props) {
  return (
    <section className="hints-wrap" aria-label="Indices">
      <div className="hints">
        {KINDS.map(({ kind, label, icon }) => {
          const hint = hints[kind];
          const state = hint.owned ? "owned" : hint.available ? "available" : "locked";
          return (
            <button
              key={kind}
              type="button"
              className={`hint ${state}`}
              disabled={state !== "available" || busy}
              onClick={() => onBuy(kind)}
              title={state === "locked" ? lockedReason(kind, tries, hints) : undefined}
            >
              <span>
                {state === "locked" ? "🔒" : icon} {label}
              </span>
              <small>{state === "owned" ? "acheté" : `${hint.price} 🪙`}</small>
            </button>
          );
        })}
      </div>
      <div className="owned">
        {KINDS.map(({ kind, label }) => {
          const hint = hints[kind];
          if (!hint.owned) return null;
          const caption = (hint.text ?? "").replace(/\*/g, "");
          return (
            <figure key={kind} className="owned-hint">
              <figcaption>{caption}</figcaption>
              {kind === "spell" && <TicketImage className="hint-img" resource="hint:spell" src="/api/hints/spell.png" alt={`Indice : ${label}`} />}
              {kind === "splash" && <TicketImage className="hint-img" resource="hint:splash" src="/api/hints/splash.png" alt={`Indice : ${label}`} />}
            </figure>
          );
        })}
      </div>
    </section>
  );
}
