import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { insideDiscord } from "./discord";
import { Grid } from "./components/Grid";
import { HintBar } from "./components/HintBar";
import { LivePanel, type LiveStatus } from "./components/LivePanel";
import { MobileCard } from "./components/MobileCard";
import { SearchBox } from "./components/SearchBox";
import { WinCard } from "./components/WinCard";
import { applyMessage, connectLive, needsRefresh } from "./live";
import type { BuyStatus, Champion, GameState, GuessStatus, HintKind, LivePlayer } from "./types";

const GUESS_MESSAGES: Partial<Record<GuessStatus, string>> = {
  unknown: "Champion inconnu : vérifie l'orthographe.",
  duplicate: "Tu as déjà tenté ce champion.",
  solved_already: "Tu as déjà trouvé aujourd'hui.",
};

const HINT_MESSAGES: Partial<Record<BuyStatus, string>> = {
  poor: "Tu n'as pas assez de FloshCoins.",
  locked: "Cet indice n'est pas encore disponible.",
  unavailable: "Indice indisponible pour le moment, réessaie plus tard.",
};

const POLL_MS = 5000;
const UNKNOWN_REFRESH_MS = 300;
const PHONE_MAX = 640;

const REVEAL_MS = 1900; // nine cells at 0.15 s apart plus the 0.45 s flip

const reducedMotion = (): boolean =>
  typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

async function loadChampions(): Promise<Champion[] | null> {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      return await api.champions();
    } catch {
      // retried once, then the game goes on without the suggestion list
    }
  }
  return null;
}

/** True under 640 px. Width-based rather than matchMedia: the reduced-motion stubs answer every query alike. */
function useIsPhone(): boolean {
  const [phone, setPhone] = useState(() => window.innerWidth < PHONE_MAX);
  useEffect(() => {
    const onResize = () => setPhone(window.innerWidth < PHONE_MAX);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return phone;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return "Doucement, réessaie dans un instant.";
    if (error.status === 403) return "Cette partie est réservée aux membres du serveur.";
  }
  return "Un souci est survenu. Réessaie dans un instant.";
}

export function App() {
  if (!insideDiscord()) {
    return (
      <main className="shell center">
        <h1 className="title">LoLdle</h1>
        <p>Ouvre le jeu depuis Discord : lance l'activité LoLdle dans un salon vocal ou avec la commande /loldle.</p>
      </main>
    );
  }
  return <Game />;
}

function Game() {
  const [state, setState] = useState<GameState | null>(null);
  const [champions, setChampions] = useState<Champion[]>([]);
  const [fatal, setFatal] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<number | null>(null);
  const [noList, setNoList] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [players, setPlayers] = useState<LivePlayer[]>([]);
  const playersRef = useRef(players);
  playersRef.current = players;
  const [meId, setMeId] = useState<string | null>(null);
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [epoch, setEpoch] = useState(0); // bumped on a new day: remounts every ticketed image
  const phone = useIsPhone();
  const revealTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (revealTimer.current) clearTimeout(revealTimer.current);
    },
    [],
  );

  // Every request that returns a state takes a number when it STARTS; a response older than the last
  // one applied is dropped, so a slow refresh cannot undo a guess played meanwhile.
  const seq = useRef(0);
  const applied = useRef(0);
  const touched = useRef(new Map<string, number>()); // player id -> number of its latest socket message
  const track = useCallback(() => ++seq.current, []);

  const applyState = useCallback((s: GameState, n: number) => {
    if (n < applied.current) return;
    applied.current = n;
    setState(s);
    // a player whose message arrived after this request started is fresher than the snapshot
    setPlayers((current) => {
      const fresher = (id: string) => (touched.current.get(id) ?? 0) > n;
      const byId = new Map(current.map((p) => [p.user_id, p]));
      const merged = s.live.map((p) => (fresher(p.user_id) ? (byId.get(p.user_id) ?? p) : p));
      for (const p of current) {
        if (fresher(p.user_id) && !s.live.some((q) => q.user_id === p.user_id)) merged.push(p);
      }
      return merged;
    });
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const id = await api.authenticate();
        const n = track();
        const [s, c] = await Promise.all([api.state(), loadChampions()]);
        if (alive) {
          setMeId(id);
          applyState(s, n);
          setChampions(c ?? []);
          setNoList(c === null);
        }
      } catch (error) {
        if (alive) setFatal(errorMessage(error));
      }
    })();
    return () => {
      alive = false;
    };
  }, [applyState, track]);

  const loaded = state !== null;

  // The live socket: one per page, closed on unmount. A reconnect refreshes the state once.
  useEffect(() => {
    if (!loaded) return;
    let alive = true;
    const refresh = () => {
      const n = track();
      api
        .state()
        .then((s) => alive && applyState(s, n))
        .catch(() => {
          // the next poll or message catches up
        });
    };
    let unknownTimer: ReturnType<typeof setTimeout> | null = null;
    const stop = connectLive(
      (msg) => {
        if (msg.type === "day") {
          if (revealTimer.current) clearTimeout(revealTimer.current);
          revealTimer.current = null;
          setRevealing(false);
          setFresh(null);
          setNotice(null);
          setEpoch((n) => n + 1);
          refresh();
        } else {
          if (needsRefresh(playersRef.current, msg) && !unknownTimer) {
            // the member's own `player` message usually follows; if not, one debounced refresh finds them
            unknownTimer = setTimeout(() => {
              unknownTimer = null;
              if (needsRefresh(playersRef.current, msg)) refresh();
            }, UNKNOWN_REFRESH_MS);
          }
          const id = msg.type === "player" ? msg.player.user_id : msg.user_id;
          touched.current.set(id, ++seq.current);
          setPlayers((current) => applyMessage(current, msg));
        }
      },
      (down) => {
        if (down) setStatus("polling");
        else refresh();
      },
      () => setStatus("live"),
    );
    return () => {
      alive = false;
      if (unknownTimer) clearTimeout(unknownTimer);
      stop();
    };
  }, [loaded, applyState, track]);

  // While no socket is open, the panel is refreshed from /api/state.
  useEffect(() => {
    if (status !== "polling") return;
    let alive = true;
    const id = setInterval(() => {
      const n = track();
      api
        .state()
        .then((s) => alive && applyState(s, n))
        .catch(() => {
          // a missed poll is retried five seconds later
        });
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [status, applyState, track]);

  const tried = useMemo(() => new Set(state?.guesses.map((g) => g.champion_id) ?? []), [state]);

  const guess = useCallback(async (text: string) => {
    setBusy(true);
    setNotice(null);
    const n = track();
    try {
      const reply = await api.guess(text);
      applyState(reply.state, n);
      const message = GUESS_MESSAGES[reply.status];
      if (message) setNotice(message);
      else {
        setFresh(Math.max(0, ...reply.state.guesses.map((g) => g.number)));
        if (reply.state.solved && !reducedMotion()) {
          // the win card names the champion: wait for the winning row to finish revealing
          setRevealing(true);
          if (revealTimer.current) clearTimeout(revealTimer.current);
          revealTimer.current = setTimeout(() => setRevealing(false), REVEAL_MS);
        }
      }
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [applyState, track]);

  const buy = useCallback(async (kind: HintKind) => {
    setBusy(true);
    setNotice(null);
    const n = track();
    try {
      const reply = await api.buyHint(kind);
      applyState(reply.state, n);
      const message = HINT_MESSAGES[reply.status];
      if (message) setNotice(message);
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [applyState, track]);

  if (fatal) {
    return (
      <main className="shell center">
        <h1 className="title">LoLdle</h1>
        <p role="alert">{fatal}</p>
      </main>
    );
  }
  if (!state) {
    return (
      <main className="shell center">
        <p>Chargement…</p>
      </main>
    );
  }

  const day = state.day.split("-").reverse().slice(0, 2).join("/");
  return (
    <main className="shell">
      <header className="top">
        <span className="title">LoLdle · {day}</span>
        <span className="meta">
          {state.guesses.length} essai{state.guesses.length > 1 ? "s" : ""} · {state.balance} 🪙
          {state.solved ? ` · 🔥 ${state.solved.streak}` : ""}
        </span>
      </header>
      <div className="room">
        <div className="play">
          {state.solved && !revealing ? (
            <WinCard solved={state.solved} version={`${state.day}:${epoch}`} />
          ) : (
            <SearchBox champions={champions} tried={tried} busy={busy || revealing} onSubmit={guess} />
          )}
          <p className="notice" role="status" aria-live="polite">
            {notice ?? (noList && !state.solved ? "La liste des champions est indisponible : tape le nom complet puis Entrée." : null)}
          </p>
          {phone ? (
            <MobileCard guesses={state.guesses} freshNumber={fresh} />
          ) : (
            <Grid guesses={state.guesses} freshNumber={fresh} />
          )}
          <HintBar key={`${state.day}:${epoch}`} hints={state.hints} tries={state.guesses.length} busy={busy} onBuy={buy} />
        </div>
        <LivePanel players={players} meId={meId} status={status} phone={phone} />
      </div>
    </main>
  );
}
