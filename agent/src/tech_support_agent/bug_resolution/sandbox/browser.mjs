import { randomBytes } from "node:crypto";
import { PolicyError } from "./files.mjs";

export const ORIGIN = "http://127.0.0.1:5173";
export class StaleRefError extends Error {}

async function disposeElements(elements) {
  await Promise.all(
    elements.map((element) => element.dispose().catch(() => {})),
  );
}

export function relativeURL(value) {
  if (
    typeof value !== "string" ||
    value.length > 500 ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    /[%\\\s\x00-\x1f\x7f-\uffff?#]/.test(value) ||
    value.split("/").some((p) => p === "." || p === "..")
  ) {
    throw new PolicyError("navigation");
  }
  if (new URL(value, ORIGIN).origin !== ORIGIN) throw new PolicyError("origin");
  return value;
}

export function allowedURL(value) {
  try {
    const url = new URL(value);
    return url.origin === ORIGIN && !url.username && !url.password;
  } catch {
    return false;
  }
}

export async function guardRoute(route) {
  if (!allowedURL(route.request().url())) return route.abort();
  // Chromium does not re-run Playwright routing for every redirect hop.
  // Never release a redirect to the browser or auto-follow it in APIRequest.
  try {
    const response = await route.fetch({ maxRedirects: 0, timeout: 10000 });
    try {
      if (response.status() >= 300 && response.status() < 400)
        return await route.abort();
      return await route.fulfill({ response });
    } finally {
      await response.dispose();
    }
  } catch {
    await route.abort().catch(() => {});
  }
}

export class Browser {
  constructor() {
    this.refs = new Map();
    this.snapshot = "";
  }

  async start() {
    const { chromium } = await import("playwright");
    this.browser = await chromium.launch({ headless: true });
    this.context = await this.browser.newContext({
      viewport: { width: 1200, height: 850 },
      serviceWorkers: "block",
      acceptDownloads: false,
    });
    await this.context.route("**/*", guardRoute);
    await this.context.routeWebSocket(/.*/, (socket) => socket.close());
    this.page = await this.context.newPage();
    this.context.on("page", (page) => {
      if (page !== this.page) void page.close();
    });
    this.page.on("dialog", (dialog) => void dialog.dismiss());
    this.page.setDefaultTimeout(5000);
    this.page.on("framenavigated", () => void this.invalidate());
    await this.navigate("/");
  }

  invalidate() {
    const elements = [...this.refs.values()];
    this.refs.clear();
    this.snapshot = "";
    return disposeElements(elements);
  }

  async navigate(relative) {
    relativeURL(relative);
    await this.invalidate();
    const response = await this.page.goto(`${ORIGIN}${relative}`, {
      waitUntil: "networkidle",
    });
    if (!response?.ok() || !allowedURL(this.page.url()))
      throw new PolicyError("navigation failed");
    return this.inspect();
  }

  async inspect() {
    await this.invalidate();
    if (!allowedURL(this.page.url())) throw new PolicyError("origin");
    // Fixed trusted evaluation only: model input is never evaluated as JavaScript.
    const revision = await this.page.evaluate(() => {
      window.__sandboxRevision ??= 0;
      if (!window.__sandboxObserver) {
        window.__sandboxObserver = new MutationObserver(
          () => window.__sandboxRevision++,
        );
        window.__sandboxObserver.observe(document.documentElement, {
          subtree: true,
          childList: true,
          attributes: true,
          characterData: true,
        });
      }
      return window.__sandboxRevision;
    });
    // Query visibility once and retain identities, never live nth locators.
    const handles = await this.page
      .locator("button:visible,select:visible,input:visible,a[href]:visible")
      .elementHandles();
    const selected = handles.slice(0, 100);
    await disposeElements(handles.slice(100));
    try {
      const nodes = await Promise.all(
        selected.map((element) =>
          element.evaluate((e) => ({
            role:
              e.getAttribute("role") ||
              {
                BUTTON: "button",
                SELECT: "combobox",
                INPUT: "input",
                A: "link",
              }[e.tagName],
            name: (
              e.getAttribute("aria-label") ||
              e.labels?.[0]?.textContent ||
              e.textContent ||
              ""
            )
              .trim()
              .slice(0, 200),
            disabled: !!e.disabled,
            value: String(e.value || "").slice(0, 100),
          })),
        ),
      );
      const view = await this.page.evaluate(() => ({
        revision: window.__sandboxRevision,
        text: document.body.innerText.slice(0, 20000),
      }));
      if (revision !== view.revision) throw new StaleRefError();
      this.revision = revision;
      this.snapshot = randomBytes(8).toString("hex");
      const elements = nodes.map((node, index) => {
        const ref = `${this.snapshot}:${index}`;
        this.refs.set(ref, selected[index]);
        return { ref, ...node };
      });
      return {
        kind: "page",
        path: new URL(this.page.url()).pathname,
        text: view.text,
        elements,
      };
    } catch (error) {
      await disposeElements(selected);
      throw error;
    }
  }

  async act(ref, value) {
    const element = this.refs.get(ref);
    const revision = this.revision;
    // The in-flight handle is owned here, not by the next snapshot/navigation.
    this.refs.delete(ref);
    await this.invalidate();
    if (!element) throw new StaleRefError();
    try {
      const fresh = await element
        .evaluate(
          (e, expected) =>
            e.isConnected && window.__sandboxRevision === expected,
          revision,
        )
        .catch(() => false);
      if (!fresh) throw new StaleRefError();
      if (value === undefined) await element.click();
      else await element.selectOption(value);
    } catch (error) {
      const connected = await element
        .evaluate((e) => e.isConnected)
        .catch(() => false);
      if (!connected) throw new StaleRefError();
      throw error;
    } finally {
      await disposeElements([element]);
    }
    await this.page.waitForTimeout(50);
    if (!allowedURL(this.page.url())) throw new PolicyError("origin");
    return this.inspect();
  }

  async screenshot() {
    if (!allowedURL(this.page.url())) throw new PolicyError("origin");
    const bytes = await this.page.screenshot({ type: "png", fullPage: false });
    if (bytes.length > 2000000) throw new PolicyError("image size");
    return {
      kind: "png",
      artifact_id: randomBytes(16).toString("hex"),
      mime: "image/png",
      data: bytes.toString("base64"),
    };
  }

  async close() {
    await this.invalidate();
    await this.browser?.close();
  }
}
