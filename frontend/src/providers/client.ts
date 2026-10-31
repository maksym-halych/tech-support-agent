import { Client } from "@langchain/langgraph-sdk";

/**
 * Returns the current Clerk session JWT, or null when the user is signed out.
 *
 * Clerk session tokens are short-lived (~60s), so this must be called per
 * request rather than resolved once and cached in a header object. With
 * `skipCache`, Clerk mints a new token with its full lifetime.
 */
export type GetToken = (options?: {
  skipCache?: boolean;
}) => Promise<string | null>;

/**
 * Requests that start a run. The agent forwards this request's token to the
 * lessons API for the whole run, so it gets a freshly minted one rather than a
 * cached token that may have only seconds left.
 */
function startsRun(url: URL, init: RequestInit): boolean {
  return (
    init.method?.toUpperCase() === "POST" &&
    /\/runs(\/stream|\/wait)?$/.test(url.pathname)
  );
}

export function createClient(
  apiUrl: string,
  apiKey: string | undefined,
  authScheme: string | undefined,
  getToken?: GetToken,
) {
  return new Client({
    apiKey,
    apiUrl,
    ...(authScheme && {
      defaultHeaders: {
        "X-Auth-Scheme": authScheme,
      },
    }),
    // `onRequest` runs immediately before every call and may be async, which is
    // what lets us mint a fresh JWT per request. A static `defaultHeaders` entry
    // would capture one token at client-construction time and go stale.
    ...(getToken && {
      onRequest: async (url: URL, init: RequestInit) => {
        const token = await getToken(
          startsRun(url, init) ? { skipCache: true } : undefined,
        );
        if (!token) return init;
        const headers = new Headers(init.headers);
        headers.set("Authorization", `Bearer ${token}`);
        return { ...init, headers };
      },
    }),
  });
}
