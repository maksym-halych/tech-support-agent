import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, symlink, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import {
  Browser,
  relativeURL,
  allowedURL,
  StaleRefError,
} from "../../src/tech_support_agent/bug_resolution/sandbox/browser.mjs";
import {
  safePath,
  readFile,
  searchCode,
  PolicyError,
} from "../../src/tech_support_agent/bug_resolution/sandbox/files.mjs";
import { validateRequest } from "../../src/tech_support_agent/bug_resolution/sandbox/runner.mjs";

test("runner rejects traversal, hidden/protected paths and foreign navigation", () => {
  for (const value of [
    "/etc/passwd",
    "../x",
    "src/../x.ts",
    "src//x.ts",
    "src/.env",
    "package.json",
    ".github/workflows/verify.yml",
    "src/a.ts;id",
    "src\\a.ts",
    "tests/new.spec.ts",
    ".git/config",
  ]) {
    assert.throws(() => safePath(value), PolicyError);
  }
  assert.equal(safePath("src/App.tsx"), "src/App.tsx");
  for (const value of [
    "https://evil.invalid",
    "//evil.invalid",
    "/\\evil",
    "/%2f%2fevil",
    "/a/../b",
    "/?url=bad",
    "/#x",
    "/\nx",
  ]) {
    assert.throws(() => relativeURL(value), PolicyError);
  }
  assert.equal(relativeURL("/"), "/");
  for (const value of [
    "file:///etc/passwd",
    "http://localhost:5173",
    "https://127.0.0.1:5173",
    "http://127.0.0.1:5174",
  ]) {
    assert.equal(allowedURL(value), false);
  }
});

test("strict JSON-lines command schema exposes no executable or shell argument", () => {
  const pack = (command) => JSON.stringify({ id: "a".repeat(32), command });
  for (const raw of [
    "{",
    "{}",
    "null",
    "[]",
    "{}\n{}",
    "x".repeat(1000001),
    pack({ op: "shell", command: "id" }),
    pack({ op: "check", kind: "tests;id" }),
    pack({ op: "inspect", script: "process.env" }),
    pack({ op: "click", ref: "button" }),
    pack({ op: "select", ref: "a".repeat(16) + ":1", value: 3 }),
    pack({ op: "prepare", files: [] }),
    pack({ op: "search_code", query: "" }),
    pack({ op: "__proto__" }),
  ]) {
    assert.throws(() => validateRequest(raw));
  }
  assert.equal(
    validateRequest(pack({ op: "search_code", query: "$(touch nope)" })).command
      .query,
    "$(touch nope)",
  );
  assert.equal(
    validateRequest(pack({ op: "check", kind: "tests" })).command.kind,
    "tests",
  );
});

test("file operations reject file and ancestor symlinks plus oversized files", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "trusted-runner-policy-"));
  try {
    await mkdir(path.join(root, "src"));
    await writeFile(path.join(root, "src/good.ts"), 'const a = "$(no shell)";');
    assert.match((await readFile("src/good.ts", root)).content, /no shell/);
    assert.equal((await searchCode("$(no shell)", root)).hits.length, 1);
    await symlink("good.ts", path.join(root, "src/link.ts"));
    await assert.rejects(readFile("src/link.ts", root), PolicyError);
    await symlink(".", path.join(root, "src/dir"));
    await assert.rejects(readFile("src/dir/good.ts", root), PolicyError);
    await assert.rejects(searchCode("const", root), PolicyError);
    await writeFile(path.join(root, "src/large.ts"), "x".repeat(65537));
    await assert.rejects(readFile("src/large.ts", root), PolicyError);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("stale refs and DOM mutations never operate on a different element", async () => {
  const browser = new Browser();
  let clicks = 0;
  let disposed = 0;
  browser.revision = 1;
  browser.refs.set("a".repeat(16) + ":0", {
    evaluate: async (_fn, expected) => expected === undefined,
    click: async () => clicks++,
    dispose: async () => disposed++,
  });
  await assert.rejects(browser.act("a".repeat(16) + ":0"), StaleRefError);
  await assert.rejects(browser.act("b".repeat(16) + ":0"), StaleRefError);
  assert.equal(clicks, 0);
  assert.equal(disposed, 1);
  assert.equal(browser.refs.size, 0);
});

test("inspect metadata and refs share fixed handles from a single visible query", async () => {
  const browser = new Browser();
  const disposed = [];
  const handles = Array.from({ length: 102 }, (_, index) => ({
    evaluate: async () => ({ name: `Control ${index}`, role: "button" }),
    dispose: async () => disposed.push(index),
  }));
  let queries = 0;
  let evaluations = 0;
  browser.page = {
    url: () => "http://127.0.0.1:5173/",
    locator: (selector) => {
      assert.equal(
        selector,
        "button:visible,select:visible,input:visible,a[href]:visible",
      );
      queries++;
      return { elementHandles: async () => handles };
    },
    evaluate: async () =>
      ++evaluations === 1 ? 7 : { revision: 7, text: "Quiz" },
  };
  const snapshot = await browser.inspect();
  assert.equal(queries, 1);
  assert.equal(snapshot.elements.length, 100);
  for (const [index, node] of snapshot.elements.entries()) {
    assert.equal(node.name, `Control ${index}`);
    assert.equal(browser.refs.get(node.ref), handles[index]);
  }
  assert.deepEqual(disposed, [100, 101]);
  await browser.invalidate();
  assert.equal(new Set(disposed).size, 102);
  assert.equal(browser.refs.size, 0);
});

test("inspect rejects a changing snapshot and releases all selected handles", async () => {
  const browser = new Browser();
  let evaluations = 0;
  let disposed = 0;
  browser.page = {
    url: () => "http://127.0.0.1:5173/",
    locator: () => ({
      elementHandles: async () => [
        {
          evaluate: async () => ({ name: "Control", role: "button" }),
          dispose: async () => disposed++,
        },
      ],
    }),
    evaluate: async () =>
      ++evaluations === 1 ? 7 : { revision: 8, text: "Changed" },
  };
  await assert.rejects(browser.inspect(), StaleRefError);
  assert.equal(browser.refs.size, 0);
  assert.equal(disposed, 1);
});

for (const value of [undefined, "3"]) {
  test(`${value === undefined ? "click" : "select"} rejects detachment during action and disposes the handle`, async () => {
    const browser = new Browser();
    let connected = true;
    let disposed = 0;
    let actions = 0;
    const action = async () => {
      assert.equal(connected, true);
      assert.equal(disposed, 0);
      assert.equal(browser.refs.size, 0);
      connected = false;
      actions++;
      throw new Error("Element is not attached to the DOM");
    };
    browser.refs.set("a".repeat(16) + ":0", {
      evaluate: async () => connected,
      click: action,
      selectOption: action,
      dispose: async () => disposed++,
    });
    await assert.rejects(
      browser.act("a".repeat(16) + ":0", value),
      StaleRefError,
    );
    assert.equal(actions, 1);
    assert.equal(disposed, 1);
  });
}
