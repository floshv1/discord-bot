import { useState } from "react";
import { cellsOf, colorClass, describeCell } from "../cells";
import { COLUMNS, type Guess } from "../types";

interface Props {
  guesses: Guess[];
  freshNumber: number | null;
}

function Older({ guess }: { guess: Guess }) {
  const [open, setOpen] = useState(false);
  const cells = cellsOf(guess);
  return (
    <li className="mrow">
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <img src={`/api/icons/${encodeURIComponent(guess.champion_id)}.png`} alt="" width={24} height={24} />
        <span className="mname">{guess.name}</span>
        <span className="dots" aria-hidden="true">
          {cells.map((c, i) => (
            <i key={i} className={`sq ${colorClass(c.color)}`} />
          ))}
        </span>
        <span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <dl className="mvalues">
          {cells.map((c, i) => (
            <div key={i} className={`mval ${colorClass(c.color)}`}>
              <dt>{COLUMNS[i]}</dt>
              <dd>{c.text}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

/** Phone layout: the nine columns do not fit, so the latest guess is a 3x3 card and the rest are chips. */
export function MobileCard({ guesses, freshNumber }: Props) {
  if (guesses.length === 0) return null;
  const sorted = [...guesses].sort((a, b) => b.number - a.number);
  const [latest, ...older] = sorted;
  return (
    <section className="mobile" aria-label="Essais">
      <div className={`mlatest${latest.number === freshNumber ? " fresh" : ""}`}>
        <p className="mhead">
          <img src={`/api/icons/${encodeURIComponent(latest.champion_id)}.png`} alt="" width={28} height={28} />
          <span>{latest.name}</span>
        </p>
        <div className="mgrid">
          {cellsOf(latest).map((cell, i) => (
            <span
              key={i}
              role="group"
              className={`mcell t ${colorClass(cell.color)}`}
              style={{ ["--i" as string]: i }}
              aria-label={describeCell(i, cell)}
            >
              <small aria-hidden="true">{COLUMNS[i]}</small>
              <span aria-hidden="true">{cell.text}</span>
            </span>
          ))}
        </div>
      </div>
      {older.length > 0 && (
        <ul className="molder">
          {older.map((g) => (
            <Older key={g.number} guess={g} />
          ))}
        </ul>
      )}
    </section>
  );
}
