import { authenticateSdk, authorizeCode } from "./discord";
import type { Champion, GameState, GuessReply, HintKind, HintReply } from "./types";

/** Carries the HTTP status only: a server message is never shown to a member. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

let session: string | null = null;
let renewing: Promise<string> | null = null;

async function send(path: string, init: RequestInit = {}, withSession = true): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  if (withSession && session) headers.set("Authorization", `Bearer ${session}`);
  return fetch(path, { ...init, headers });
}

/** Authorize with Discord, trade the code for a session, tell the SDK. Returns the user id. */
export async function authenticate(): Promise<string> {
  const code = await authorizeCode();
  const response = await send("/api/auth", { method: "POST", body: JSON.stringify({ code }) }, false);
  if (!response.ok) throw new ApiError(response.status);
  const body = (await response.json()) as { access_token: string; session: string; user_id: string };
  session = body.session;
  await authenticateSdk(body.access_token);
  return body.user_id;
}

async function renew(): Promise<void> {
  // Concurrent 401s share one renewal: the auth route has a shared rate limit.
  renewing ??= authenticate().finally(() => {
    renewing = null;
  });
  await renewing;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response = await send(path, init);
  if (response.status === 401) {
    await renew();
    response = await send(path, init);
  }
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as T;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  authenticate,
  state: () => call<GameState>("/api/state"),
  champions: () => call<Champion[]>("/api/champions"),
  guess: (champion: string) => call<GuessReply>("/api/guess", post({ champion })),
  buyHint: (kind: HintKind) => call<HintReply>(`/api/hints/${kind}`, post({})),
  async ticket(resource: string): Promise<string> {
    return (await call<{ ticket: string }>("/api/tickets", post({ resource }))).ticket;
  },
};

/** Test hook: forget the session between cases. */
export function resetSession(): void {
  session = null;
  renewing = null;
}
