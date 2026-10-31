import { readdir, readFile, lstat } from "node:fs/promises";
import path from "node:path";
import { ROOT, MAX_FILE, HARNESS, safePath } from "./files.mjs";

async function processes() {
  const found = [];
  for (const name of await readdir("/proc")) {
    if (!/^[0-9]+$/.test(name)) continue;
    const pid = Number(name);
    if (pid === 1 || pid === process.pid) continue;
    try {
      const stat = await readFile(`/proc/${pid}/stat`, "utf8");
      found.push({ pid, state: stat.slice(stat.lastIndexOf(")") + 2).split(" ")[0] });
    } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  return found;
}

async function main() {
  // PID 1 is the image's Docker --init reaper. Only this image-owned exporter
  // remains runnable; even the JSON-lines runner is stopped before any read.
  let quiet = false;
  for (let round = 0; round < 100; round++) {
    for (const { pid } of await processes()) {
      try { process.kill(pid, "SIGSTOP"); }
      catch (error) { if (error.code !== "ESRCH") throw error; }
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
    if ((await processes()).every(({ state }) => ["T", "t", "Z", "X"].includes(state))) {
      quiet = true;
      break;
    }
  }
  if (!quiet) throw new Error("quiescence not confirmed");
  const files = [];
  let entries = 0;
  async function walk(directory = "") {
    for (const name of await readdir(path.join(ROOT, directory))) {
      if (++entries > 500) throw new Error("entry budget");
      const relative = directory ? `${directory}/${name}` : name;
      const target = path.join(ROOT, relative);
      const stat = await lstat(target);
      if (relative === "node_modules") {
        const { readlink } = await import("node:fs/promises");
        if (!stat.isSymbolicLink() || await readlink(target) !== "/opt/deps/node_modules") throw new Error("dependency link changed");
        continue;
      }
      if (stat.isSymbolicLink()) throw new Error("snapshot link");
      if (stat.isDirectory()) { await walk(relative); continue; }
      if (!stat.isFile() || stat.nlink !== 1) throw new Error("snapshot not regular");
      if (relative.startsWith("dist/")) continue;
      if (!HARNESS.has(relative)) safePath(relative);
      if (stat.size > MAX_FILE) throw new Error("file budget");
      const bytes = await readFile(target);
      if (bytes.length > MAX_FILE) throw new Error("file budget");
      files.push({ path: relative, content: new TextDecoder("utf-8", { fatal: true }).decode(bytes) });
    }
  }
  await walk();
  if (!(await processes()).every(({ state }) => ["T", "t", "Z", "X"].includes(state))) throw new Error("quiescence lost");
  const output = JSON.stringify({ files });
  if (Buffer.byteLength(output) > 2000000) throw new Error("snapshot budget");
  process.stdout.write(output);
}

main().catch((error) => { process.stderr.write(String(error)); process.exitCode = 1; });
