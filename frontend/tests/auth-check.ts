import { readFileSync, realpathSync } from "node:fs";
import { isAbsolute, resolve, sep } from "node:path";

export default function authCheck() {
  const filename = process.env.BUG_E2E_STORAGE_STATE;
  if (!filename || !isAbsolute(filename)) {
    throw new Error(
      "BLOCKED: BUG_E2E_STORAGE_STATE must name an absolute, external Playwright storage-state file from an authorized Clerk test-user sign-in. No auth bypass is provided.",
    );
  }
  const actual = realpathSync(filename);
  const root = resolve("..");
  if (actual === root || actual.startsWith(root + sep)) {
    throw new Error("Keep the Clerk session outside the repository.");
  }
  const state = JSON.parse(readFileSync(actual, "utf8"));
  if (!Array.isArray(state.cookies) || !state.cookies.length) {
    throw new Error(
      "BLOCKED: Clerk test storage state has no session cookies.",
    );
  }
  if (
    !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.startsWith("pk_test_") ||
    !process.env.CLERK_SECRET_KEY?.startsWith("sk_test_")
  ) {
    throw new Error(
      "BLOCKED: use a separate Clerk test instance, not production keys.",
    );
  }
}
