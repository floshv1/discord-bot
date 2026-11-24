import { useId, useState } from "react";
import { STATE_LABEL, colorClass } from "../cells";
import { sortPlayers } from "../live";
import type { LivePlayer } from "../types";

export type LiveStatus = "connecting" | "live" | "polling";

interface Props {
  players: LivePlayer[];
  meId: string | null;
  status: LiveStatus;
  /** Phone layout: the panel collapses behind a toggle. */
  phone: boolean;
}

const STATUS_TEXT: Record<LiveStatus, string> = {
  connecting: "Connexion…",
  live: "En direct",
  polling: "Mise à jour toutes les 5 s",
};

/** Up to two initials; Discord avatars are not loaded (the activity's CSP would block the CDN). */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = (words.length > 1 ? [words[0], words[1]] : [words[0] ?? "?"]).map((w) => Array.from(w)[0]);
  return letters.join("").toUpperCase();
}

const hue = (id: string): number => [...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);

function Row({ player, me }: { player: LivePlayer; me: boolean }) {
  const summary = player.last_cells.map((c) => STATE_LABEL[c]).join(", ");
  return (
    <li className={`pl${player.online ? "" : " off"}`}>
      <span className="pav" style={{ background: `hsl(${hue(player.user_id)} 35% 38%)` }} aria-hidden="true">
        {initials(player.name)}
      </span>
      <span className="pbody">
        <span className="pname">
          <span className="pn">{player.name}</span>
          {me && <small className="me">toi</small>}
          {player.solved && (
            <span role="img" aria-label="a trouvé">
              ✅
            </span>
          )}
        </span>
        <span className="ptries">
          {player.tries} essai{player.tries > 1 ? "s" : ""}
          {player.online ? "" : " · hors ligne"}
        </span>
        {player.last_cells.length > 0 && (
          <span className="dots" role="img" aria-label={`Dernier essai : ${summary}`}>
            {player.last_cells.map((color, i) => (
              <i key={i} className={`sq ${colorClass(color)}`} />
            ))}
          </span>
        )}
      </span>
    </li>
  );
}

export function LivePanel({ players, meId, status, phone }: Props) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const sorted = sortPlayers(players);
  const online = players.filter((p) => p.online).length;
  const showBody = !phone || open;

  return (
    <aside className="rail" aria-label="Salon en direct">
      {phone ? (
        <button type="button" className="rail-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((v) => !v)}>
          <b>Dans le salon</b>
          <span>
            {online} en ligne {open ? "▴" : "▾"}
          </span>
          <small className={`rail-status ${status}`}>{STATUS_TEXT[status]}</small>
        </button>
      ) : (
        <b className="rail-title">Dans le salon</b>
      )}
      {showBody && (
        <div id={bodyId}>
          {sorted.length === 0 ? (
            <p className="note">Personne d'autre pour l'instant.</p>
          ) : (
            <ul className="plist">
              {sorted.map((p) => (
                <Row key={p.user_id} player={p} me={p.user_id === meId} />
              ))}
            </ul>
          )}
          {!phone && (
            <p className={`rail-status ${status}`} role="status">
              {STATUS_TEXT[status]}
            </p>
          )}
        </div>
      )}
    </aside>
  );
}
