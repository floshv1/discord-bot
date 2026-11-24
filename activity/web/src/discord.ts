import { DiscordSDK } from "@discord/embedded-app-sdk";

let sdk: DiscordSDK | null = null;
let clientId: string | null = null;

/** The id is a runtime setting served by the container, so one image fits every deployment. */
async function loadClientId(): Promise<string> {
  if (clientId === null) {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error(`config HTTP ${response.status}`);
    const body = (await response.json()) as { client_id?: unknown };
    if (typeof body.client_id !== "string" || !body.client_id) throw new Error("config without client_id");
    clientId = body.client_id;
  }
  return clientId;
}

/** Tests only: forget the SDK and the id between cases. */
export function resetDiscord(): void {
  sdk = null;
  clientId = null;
}

/** Discord opens an Activity with `frame_id` in the query string; anywhere else there is nothing to talk to. */
export function insideDiscord(): boolean {
  return new URLSearchParams(window.location.search).has("frame_id");
}

async function ready(): Promise<DiscordSDK> {
  if (sdk === null) {
    const created = new DiscordSDK(await loadClientId());
    await created.ready();
    sdk = created;
  }
  return sdk;
}

/** First leg of the login: the OAuth code the server exchanges. */
export async function authorizeCode(): Promise<string> {
  const discord = await ready();
  const { code } = await discord.commands.authorize({
    client_id: await loadClientId(),
    response_type: "code",
    state: "",
    prompt: "none",
    scope: ["identify", "guilds.members.read"],
  });
  return code;
}

/** Last leg: tells the SDK who we are, with the token the server obtained. */
export async function authenticateSdk(accessToken: string): Promise<void> {
  const discord = await ready();
  await discord.commands.authenticate({ access_token: accessToken });
}
