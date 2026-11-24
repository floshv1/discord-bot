import type { Solved } from "../types";
import { TicketImage } from "./TicketImage";

/** `version` changes with the day: the splash is remounted so no stale ticket or failed state survives. */
export function WinCard({ solved, version = "" }: { solved: Solved; version?: string }) {
  return (
    <section className="win" aria-label="Victoire">
      <TicketImage key={version} className="splash" resource="splash" src="/api/result/splash.png" alt={solved.name} />
      <div className="win-body">
        <p className="kicker">Trouvé !</p>
        <h2>{solved.name}</h2>
        <dl>
          <div>
            <dt>Essais</dt>
            <dd>{solved.tries}</dd>
          </div>
          <div>
            <dt>Score</dt>
            <dd>{solved.score}</dd>
          </div>
          <div>
            <dt>Série</dt>
            <dd>🔥 {solved.streak}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
