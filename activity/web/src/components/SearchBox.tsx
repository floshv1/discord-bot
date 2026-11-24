import { useMemo, useState, type KeyboardEvent } from "react";
import { searchChampions } from "../search";
import type { Champion } from "../types";

interface Props {
  champions: Champion[];
  tried: ReadonlySet<string>;
  /** A request is in flight: the input stays focusable (readOnly) so the keyboard flow survives. */
  busy?: boolean;
  /** Receives the champion's name (or the raw text); the server decides whether it exists. */
  onSubmit: (text: string) => void;
}

export function SearchBox({ champions, tried, busy = false, onSubmit }: Props) {
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const results = useMemo(() => searchChampions(champions, text, tried), [champions, text, tried]);
  const open = results.length > 0;

  const choosable = (i: number) => results[i] !== undefined && !results[i].tried;
  const firstChoosable = () => results.findIndex((r) => !r.tried);

  function move(step: 1 | -1) {
    if (results.length === 0) return;
    let next = active;
    for (let n = 0; n < results.length; n++) {
      next = (next + step + results.length) % results.length;
      if (choosable(next)) break;
    }
    setActive(next);
  }

  function submit(value: string) {
    if (busy || !value.trim()) return;
    onSubmit(value);
    setText("");
    setActive(0);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (open) {
        const index = choosable(active) ? active : firstChoosable();
        if (index >= 0) submit(results[index].name);
        // Only tried champions match: nothing to submit, the greyed rows already say why.
      } else {
        submit(text);
      }
    } else if (event.key === "Escape") {
      setText("");
    }
  }

  return (
    <div className="search">
      <input
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls="search-results"
        aria-activedescendant={open && choosable(active) ? `opt-${active}` : undefined}
        aria-label="Nom du champion"
        placeholder="🔍 Tape un champion…"
        autoComplete="off"
        spellCheck={false}
        readOnly={busy}
        aria-busy={busy}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul id="search-results" role="listbox" className="results">
          {results.map((r, i) => (
            <li
              key={r.id}
              id={`opt-${i}`}
              role="option"
              aria-selected={i === active && !r.tried}
              aria-disabled={r.tried}
              className={`${i === active && !r.tried ? "active" : ""}${r.tried ? " tried" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault(); // keep focus in the input
                if (!r.tried) submit(r.name);
              }}
            >
              <img src={`/api/icons/${encodeURIComponent(r.id)}.png`} alt="" width={24} height={24} />
              <span>{r.name}</span>
              {r.tried && <small>déjà tenté</small>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
