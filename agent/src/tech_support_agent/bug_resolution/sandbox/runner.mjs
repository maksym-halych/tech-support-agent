import { spawn } from "node:child_process";
import {
  mkdir,
  readFile as readRaw,
  symlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { Browser, relativeURL, StaleRefError } from "./browser.mjs";
import {
  HARNESS,
  MAX_FILE,
  PolicyError,
  ROOT,
  readFile,
  safePath,
  searchCode,
} from "./files.mjs";
import { boundedProcess, CHILD_ENV, killGroup } from "./process.mjs";

export function validateRequest(raw) {
  if (Buffer.byteLength(raw) > 1000000) throw new PolicyError("line size");
  const request = JSON.parse(raw);
  if (
    !request ||
    typeof request !== "object" ||
    Object.keys(request).sort().join() !== "command,id" ||
    !/^[a-f0-9]{32}$/.test(request.id) ||
    typeof request.id !== "string"
  )
    throw new PolicyError("envelope");
  const c = request.command;
  const fields = {
    inspect: [],
    screenshot: [],
    navigate: ["path"],
    click: ["ref"],
    select: ["ref", "value"],
    read_file: ["path"],
    search_code: ["query"],
    check: ["kind"],
    prepare: ["files"],
  };
  if (
    !c ||
    typeof c !== "object" ||
    !Object.hasOwn(fields, c.op) ||
    Object.keys(c).sort().join() !== ["op", ...fields[c.op]].sort().join()
  )
    throw new PolicyError("command");
  if (c.op === "navigate") relativeURL(c.path);
  if (c.op === "read_file") safePath(c.path);
  if (
    ["click", "select"].includes(c.op) &&
    (typeof c.ref !== "string" || !/^[a-f0-9]{16}:[0-9]{1,3}$/.test(c.ref))
  )
    throw new PolicyError("ref");
  for (const field of ["value", "query"]) {
    if (
      Object.hasOwn(c, field) &&
      (typeof c[field] !== "string" ||
        !c[field].length ||
        c[field].length > 100)
    )
      throw new PolicyError(field);
  }
  if (c.op === "check" && !["typecheck", "tests", "build"].includes(c.kind))
    throw new PolicyError("check");
  if (c.op === "prepare") {
    if (!Array.isArray(c.files) || !c.files.length || c.files.length > 100)
      throw new PolicyError("files");
    const seen = new Set();
    for (const file of c.files) {
      if (
        !file ||
        Object.keys(file).sort().join() !== "content,path" ||
        typeof file.content !== "string" ||
        Buffer.byteLength(file.content) > MAX_FILE ||
        seen.has(file.path)
      )
        throw new PolicyError("file");
      if (!HARNESS.has(file.path)) safePath(file.path);
      seen.add(file.path);
    }
    if ([...HARNESS].some((p) => !seen.has(p)))
      throw new PolicyError("missing harness");
  }
  return request;
}

class Runner {
  constructor() {
    this.browser = new Browser();
    this.prepared = false;
  }

  async prepare(files) {
    if (this.prepared) throw new PolicyError("already prepared");
    this.prepared = true;
    for (const file of files) {
      if (
        HARNESS.has(file.path) &&
        file.content !== (await readRaw(`/opt/harness/${file.path}`, "utf8"))
      )
        throw new PolicyError("harness mismatch");
    }
    for (const file of files) {
      const target = path.join(ROOT, file.path);
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, file.content, { flag: "wx", mode: 0o444 });
    }
    await symlink("/opt/deps/node_modules", `${ROOT}/node_modules`);
    this.vite = spawn(
      "/usr/local/bin/node",
      [
        "/opt/deps/node_modules/vite/bin/vite.js",
        "--configLoader",
        "runner",
        "--host",
        "0.0.0.0",
        "--port",
        "5173",
        "--strictPort",
      ],
      { cwd: ROOT, env: CHILD_ENV, detached: true, stdio: "ignore" },
    );
    this.vite.on("error", () => {
      this.startFailed = true;
    });
    for (let i = 0; i < 150; i++) {
      if (this.startFailed || this.vite.exitCode !== null)
        throw new Error("Vite exited");
      try {
        if (
          (
            await fetch("http://127.0.0.1:5173", {
              signal: AbortSignal.timeout(500),
            })
          ).ok
        )
          break;
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    await this.browser.start();
    return { kind: "ready" };
  }

  async dispatch(c) {
    if (c.op === "prepare") return this.prepare(c.files);
    if (!this.prepared) throw new PolicyError("not prepared");
    switch (c.op) {
      case "inspect":
        return this.browser.inspect();
      case "navigate":
        return this.browser.navigate(c.path);
      case "click":
        return this.browser.act(c.ref);
      case "select":
        return this.browser.act(c.ref, c.value);
      case "screenshot":
        return this.browser.screenshot();
      case "read_file":
        return readFile(c.path);
      case "search_code":
        return searchCode(c.query);
      case "check": {
        const args = {
          typecheck: ["/opt/deps/node_modules/typescript/bin/tsc", "--noEmit"],
          tests: ["/opt/deps/node_modules/@playwright/test/cli.js", "test"],
          build: [
            "/opt/deps/node_modules/vite/bin/vite.js",
            "build",
            "--configLoader",
            "runner",
          ],
        };
        return {
          kind: "check",
          check: c.kind,
          ...(await boundedProcess("/usr/local/bin/node", args[c.kind], {
            cwd: ROOT,
          })),
        };
      }
    }
  }

  async close() {
    await this.browser.close();
    if (this.vite?.pid) killGroup(this.vite);
  }
}

async function main() {
  const runner = new Runner();
  let buffer = Buffer.alloc(0);
  try {
    for await (const chunk of process.stdin) {
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length > 1000000) throw new Error("oversized input");
      let newline;
      while ((newline = buffer.indexOf(10)) >= 0) {
        const line = buffer.subarray(0, newline).toString("utf8");
        buffer = buffer.subarray(newline + 1);
        // A malformed envelope has no trustworthy ID: terminate the channel.
        const request = validateRequest(line);
        let reply;
        try {
          reply = {
            id: request.id,
            result: await runner.dispatch(request.command),
          };
        } catch (error) {
          process.stderr.write(`${String(error).slice(0, 2000)}\n`);
          reply = {
            id: request.id,
            error:
              error instanceof StaleRefError
                ? "stale_ref"
                : error instanceof PolicyError
                  ? "policy"
                  : "operation_failed",
          };
        }
        const output = JSON.stringify(reply);
        if (Buffer.byteLength(output) > 3000000)
          throw new Error("oversized output");
        await new Promise((resolve, reject) =>
          process.stdout.write(`${output}\n`, (error) =>
            error ? reject(error) : resolve(),
          ),
        );
      }
    }
    if (buffer.length) throw new Error("incomplete input");
  } finally {
    await runner.close();
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch(() => {
    process.stderr.write(
      "Runner stopped: invalid input or infrastructure failure\n",
    );
    process.exitCode = 1;
  });
}
