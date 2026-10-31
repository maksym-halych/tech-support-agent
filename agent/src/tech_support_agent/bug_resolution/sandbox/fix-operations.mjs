import { constants } from "node:fs";
import { lstat, mkdir, open, rename, unlink } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { MAX_FILE, PolicyError, ROOT, safePath } from "./files.mjs";
import { boundedProcess } from "./process.mjs";

async function write(change) {
  if (
    !change || Object.keys(change).sort().join() !== "content,path" ||
    typeof change.content !== "string" || Buffer.byteLength(change.content) > MAX_FILE
  ) throw new PolicyError("change");
  safePath(change.path);
  if (!change.path.startsWith("src/") && change.path !== "tests/regression.spec.ts")
    throw new PolicyError("protected");
  let parent = ROOT;
  if (!(await lstat(parent)).isDirectory() || (await lstat(parent)).isSymbolicLink())
    throw new PolicyError("root");
  const segments = change.path.split("/");
  for (const segment of segments.slice(0, -1)) {
    parent = path.join(parent, segment);
    try { await mkdir(parent); } catch (error) { if (error.code !== "EEXIST") throw error; }
    const stat = await lstat(parent);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new PolicyError("parent");
  }
  const target = path.join(ROOT, change.path);
  try {
    const stat = await lstat(target);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) throw new PolicyError("target");
  } catch (error) { if (error.code !== "ENOENT") throw error; }
  const temporary = path.join(parent, `.edit-${randomUUID()}`);
  const handle = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o444);
  try {
    await handle.writeFile(change.content, "utf8");
    await handle.close();
    await rename(temporary, target);
  } finally {
    await handle.close();
    await unlink(temporary).catch((error) => { if (error.code !== "ENOENT") throw error; });
  }
  return { written: change.path };
}

async function main() {
  let data = Buffer.alloc(0);
  for await (const chunk of process.stdin) {
    data = Buffer.concat([data, chunk]);
    if (data.length > 400000) throw new PolicyError("size");
  }
  const command = JSON.parse(data.toString("utf8"));
  let result;
  if (command.op === "write" && Object.keys(command).sort().join() === "change,op") {
    result = await write(command.change);
  } else if (command.op === "tests" && Object.keys(command).sort().join() === "op,regression" && typeof command.regression === "boolean") {
    result = await boundedProcess("/usr/local/bin/node", [
      "/opt/deps/node_modules/@playwright/test/cli.js", "test",
      ...(command.regression ? ["tests/regression.spec.ts"] : []),
      "--reporter=/opt/runner/fix-reporter.mjs", "--forbid-only", "--retries=0",
    ], { cwd: ROOT });
  } else { throw new PolicyError("operation"); }
  process.stdout.write(JSON.stringify(result));
}

main().catch((error) => { process.stderr.write(String(error).slice(0, 2000)); process.exitCode = 1; });
