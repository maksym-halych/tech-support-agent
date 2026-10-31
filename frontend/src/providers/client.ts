import { Client } from "@langchain/langgraph-sdk";

/**
 * Returns the current Clerk session JWT, or null when the user is signed out.
 *
 * Clerk session tokens are short-lived (~60s), so this must be called per
 * request rather than resolved once and cached in a header object.
 */
export type GetToken = () => Promise<string | null>;

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
      onRequest: async (_url: URL, init: RequestInit) => {
        const token = await getToken();
        if (!token) return init;
        const headers = new Headers(init.headers);
        headers.set("Authorization", `Bearer ${token}`);
        return { ...init, headers };
      },
    }),
  });
}
