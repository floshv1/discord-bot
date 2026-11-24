import { cellsOf, colorClass, describeCell } from "../cells";
import { COLUMNS, type Guess } from "../types";

interface Props {
  guesses: Guess[];
  /** Number of the guess to reveal cell by cell; the others are shown as they are. */
  freshNumber?: number | null;
}

export function Grid({ guesses, freshNumber = null }: Props) {
  const rows = [...guesses].sort((a, b) => b.number - a.number); // newest on top
  return (
    <div className="grid9-wrap" role="table" aria-label="Essais">
      <div className="grid9 head-row" role="row">
        <span role="columnheader" className="head">
          <span className="sr-only">Champion</span>
        </span>
        {COLUMNS.map((name) => (
          <span key={name} role="columnheader" className="head">
            {name}
          </span>
        ))}
      </div>
      {rows.map((guess) => (
        <div
          key={guess.number}
          className={`grid9${guess.number === freshNumber ? " fresh" : ""}`}
          role="row"
          data-number={guess.number}
        >
          <span role="cell" className="av" title={guess.name}>
            <img src={`/api/icons/${encodeURIComponent(guess.champion_id)}.png`} alt={guess.name} width={28} height={28} />
          </span>
          {cellsOf(guess).map((cell, i) => (
            <span
              key={i}
              role="cell"
              className={`t ${colorClass(cell.color)}`}
              style={{ ["--i" as string]: i }}
              aria-label={describeCell(i, cell)}
            >
              {cell.text}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}
