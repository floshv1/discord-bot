import { beforeEach, expect, test, vi } from "vitest";

const ctor = vi.fn();
const authorize = vi.fn(async () => ({ code: "c1" }));

vi.mock("@discord/embedded-app-sdk", () => ({
  DiscordSDK: class {
    commands = { authorize, authenticate: vi.fn(async () => undefined) };
    constructor(id: string) {
      ctor(id);
    }
    async ready() {}
  },
}));

import { authorizeCode, resetDiscord } from "./discord";

beforeEach(() => {
  resetDiscord();
  ctor.mockClear();
  authorize.mockClear();
});

test("l'identifiant client vient du serveur à l'exécution, pas de la compilation", async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ client_id: "runtime-id" }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  expect(await authorizeCode()).toBe("c1");
  await authorizeCode();

  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith("/api/config");
  expect(ctor).toHaveBeenCalledWith("runtime-id");
  expect(authorize).toHaveBeenCalledWith(expect.objectContaining({ client_id: "runtime-id" }));
});

test("un serveur non configuré fait échouer la connexion sans jamais créer le SDK", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
  await expect(authorizeCode()).rejects.toThrow();
  expect(ctor).not.toHaveBeenCalled();
});
