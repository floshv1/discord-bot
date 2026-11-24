import { beforeEach, expect, test, vi } from "vitest";

vi.mock("./discord", () => ({
  authorizeCode: vi.fn(async () => "code-1"),
  authenticateSdk: vi.fn(async () => undefined),
}));

import { api, ApiError, resetSession } from "./api";
import { authenticateSdk, authorizeCode } from "./discord";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  resetSession();
  vi.mocked(authorizeCode).mockClear();
  vi.mocked(authenticateSdk).mockClear();
});

test("api renouvelle la session une fois sur 401 puis réessaie", async () => {
  const sessions = ["s1", "s2"];
  const seen: string[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/auth") return json(200, { access_token: "tok", session: sessions.shift(), user_id: "42" });
    const auth = new Headers(init?.headers).get("Authorization") ?? "";
    seen.push(auth);
    return auth === "Bearer s2" ? json(200, { day: "2026-11-24" }) : json(401, { error: "unauthorized" });
  });
  vi.stubGlobal("fetch", fetchMock);

  await api.authenticate();
  const state = await api.state();

  expect(state.day).toBe("2026-11-24");
  expect(seen).toEqual(["Bearer s1", "Bearer s2"]);
  expect(authorizeCode).toHaveBeenCalledTimes(2);
  expect(authenticateSdk).toHaveBeenLastCalledWith("tok");
});

test("un second 401 n'est pas réessayé en boucle", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/auth" ? json(200, { access_token: "t", session: "s", user_id: "1" }) : json(401, {}),
    ),
  );
  await expect(api.state()).rejects.toMatchObject({ status: 401 });
  expect(authorizeCode).toHaveBeenCalledTimes(1);
});

test("une erreur serveur ne transporte que le statut", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(500, { error: "internal", detail: "secret" })));
  const error = await api.state().catch((e) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(500);
  expect(error.message).not.toContain("secret");
});
