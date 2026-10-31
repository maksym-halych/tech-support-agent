import { constants } from "node:fs";
import { lstat, open, readdir, realpath } from "node:fs/promises";
import path from "node:path";

export const MAX_FILE = 65536;
export const ROOT = "/work/project";
export const HARNESS = new Set([
  "index.html",
  "package.json",
  "pnpm-lock.yaml",
  "tsconfig.json",
  "vite.config.ts",
  "playwright.config.ts",
  "tests/smoke.spec.ts",
  ".github/workflows/verify.yml",
  ".gitignore",
]);

export class PolicyError extends Error {}

export function safePath(value) {
  if (
    typeof value !== "string" ||
    !value ||
    value.length > 200 ||
    !/^[A-Za-z0-9_./-]+$/.test(value) ||
    value
      .split("/")
      .some(
        (p) => !p || p.startsWith(".") || ["node_modules", "dist"].includes(p),
      ) ||
    !(
      (value.startsWith("src/") && /\.(ts|tsx|css)$/.test(value)) ||
      ["tests/smoke.spec.ts", "tests/regression.spec.ts"].includes(value)
    )
  ) {
    throw new PolicyError("path");
  }
  return value;
}

export async function regularPath(relative, root = ROOT) {
  safePath(relative);
  let current = root;
  if ((await lstat(root)).isSymbolicLink()) throw new PolicyError("symlink");
  for (const segment of relative.split("/")) {
    current = path.join(current, segment);
    if ((await lstat(current)).isSymbolicLink())
      throw new PolicyError("symlink");
  }
  if (
    !(await lstat(current)).isFile() ||
    !(await realpath(current)).startsWith(`${await realpath(root)}/`)
  ) {
    throw new PolicyError("not regular");
  }
  return current;
}

export async function readFile(relative, root = ROOT) {
  const filename = await regularPath(relative, root);
  const handle = await open(
    filename,
    constants.O_RDONLY | constants.O_NOFOLLOW,
  );
  try {
    if ((await handle.stat()).size > MAX_FILE) throw new PolicyError("size");
    const buffer = Buffer.alloc(MAX_FILE + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    if (bytesRead > MAX_FILE) throw new PolicyError("size");
    return {
      kind: "file",
      path: relative,
      content: new TextDecoder("utf-8", { fatal: true }).decode(
        buffer.subarray(0, bytesRead),
      ),
    };
  } finally {
    await handle.close();
  }
}

export async function searchCode(query, root = ROOT) {
  const hits = [];
  let visited = 0;
  async function walk(directory) {
    for (const entry of await readdir(path.join(root, directory), {
      withFileTypes: true,
    })) {
      if (++visited > 300) throw new PolicyError("file count");
      const relative = `${directory}/${entry.name}`;
      if (entry.isSymbolicLink()) throw new PolicyError("symlink");
      if (entry.isDirectory()) {
        await walk(relative);
        continue;
      }
      if (!/\.(ts|tsx|css)$/.test(relative)) continue;
      const file = await readFile(relative, root);
      file.content.split("\n").forEach((line, i) => {
        if (line.includes(query) && hits.length < 100)
          hits.push({ path: relative, line: i + 1, text: line.slice(0, 300) });
      });
    }
  }
  await walk("src");
  return { kind: "search", hits };
}
