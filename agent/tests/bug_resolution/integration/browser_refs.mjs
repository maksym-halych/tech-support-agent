import assert from "node:assert/strict";
import http from "node:http";
import { Browser, StaleRefError } from "/opt/runner/browser.mjs";

// This support-only harness is passed to Node inside Docker, never the demo image.
const server = http.createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end("<html><body>Browser reference regressions</body></html>");
});
await new Promise((resolve) => server.listen(5173, "127.0.0.1", resolve));
const browser = new Browser();

async function content(html) {
  await browser.page.setContent(html);
  await browser.page.evaluate(() => {
    window.events = [];
    document.addEventListener("click", (event) =>
      window.events.push(event.target.id),
    );
    document.addEventListener("change", (event) =>
      window.events.push(event.target.id),
    );
  });
  return browser.inspect();
}

function ref(snapshot, name) {
  const matches = snapshot.elements.filter((element) => element.name === name);
  assert.equal(matches.length, 1, name);
  return matches[0].ref;
}

async function events() {
  return browser.page.evaluate(() => window.events);
}

try {
  await browser.start();
  const hidden = `
    <button style="visibility:hidden">Hidden button</button>
    <select aria-label="Hidden select" style="visibility:hidden"><option>0</option></select>
    <button style="display:none">Display none</button>
    <div style="visibility:hidden"><button>Inherited hidden</button></div>
  `;
  let snapshot = await content(`${hidden}
    <button id="first">First</button><button id="second">Second</button>
    <select id="grade" aria-label="Grade"><option value="">Choose</option><option value="3">3</option></select>
  `);
  assert.deepEqual(
    snapshot.elements.map((element) => element.name),
    ["First", "Second", "Grade"],
  );
  snapshot = await browser.act(ref(snapshot, "First"));
  assert.deepEqual(await events(), ["first"]);
  snapshot = await browser.act(ref(snapshot, "Second"));
  assert.deepEqual(await events(), ["first", "second"]);
  await browser.act(ref(snapshot, "Grade"), "3");
  assert.deepEqual(await events(), ["first", "second", "grade"]);
  assert.equal(await browser.page.locator("#grade").inputValue(), "3");

  for (const action of ["click", "selectOption"]) {
    const select = action === "selectOption";
    const value = select ? "3" : undefined;
    const target = select
      ? '<select id="target" aria-label="Target"><option value="">Choose</option><option value="3">3</option></select>'
      : '<button id="target">Target</button>';
    const sibling = select
      ? '<select id="sibling" aria-label="Sibling"><option value="">Choose</option><option value="3">3</option></select>'
      : '<button id="sibling">Sibling</button>';

    // The target must remain the same node even if its DOM index changes after
    // the freshness check but before Playwright starts its action.
    snapshot = await content(target + sibling);
    let targetRef = ref(snapshot, "Target");
    let handle = browser.refs.get(targetRef);
    let originalAction = handle[action].bind(handle);
    handle[action] = async (...args) => {
      await browser.page.evaluate(() => {
        document
          .querySelector("#target")
          .before(document.querySelector("#sibling"));
      });
      return originalAction(...args);
    };
    await browser.act(targetRef, value);
    assert.deepEqual(
      await events(),
      ["target"],
      `${action} retargeted after reorder`,
    );

    for (const mutation of ["detach", "replace"]) {
      // A disabled original guarantees that actionability cannot finish before
      // our barrier. The replacement and following sibling are both actionable.
      snapshot = await content(
        target.replace('id="target"', 'id="target" disabled') + sibling,
      );
      targetRef = ref(snapshot, "Target");
      handle = browser.refs.get(targetRef);
      originalAction = handle[action].bind(handle);
      const originalDispose = handle.dispose.bind(handle);
      let disposed = 0;
      handle.dispose = async () => {
        disposed++;
        await originalDispose();
      };
      let started;
      const actionStarted = new Promise((resolve) => {
        started = resolve;
      });
      let settled = false;
      handle[action] = (...args) => {
        const pending = originalAction(...args);
        started();
        return pending;
      };
      const pending = browser.act(targetRef, value);
      const rejected = assert.rejects(pending, StaleRefError);
      void pending.then(
        () => {
          settled = true;
        },
        () => {
          settled = true;
        },
      );
      await actionStarted;
      await browser.page.evaluate(
        () =>
          new Promise((resolve) => {
            requestAnimationFrame(() => requestAnimationFrame(resolve));
          }),
      );
      assert.equal(
        settled,
        false,
        "original must still be waiting for actionability",
      );
      await browser.page.evaluate((mutation) => {
        const target = document.querySelector("#target");
        if (mutation === "detach") target.remove();
        else {
          const replacement = target.cloneNode(true);
          replacement.disabled = false;
          target.replaceWith(replacement);
        }
      }, mutation);
      await rejected;
      assert.deepEqual(
        await events(),
        [],
        `${action} acted on ${mutation} replacement/sibling`,
      );
      assert.equal(browser.refs.size, 0);
      assert.equal(disposed, 1);
      // Recover only with a new inspect, proving the replacement itself works.
      if (mutation === "replace") {
        snapshot = await browser.inspect();
        await browser.act(ref(snapshot, "Target"), value);
        assert.deepEqual(await events(), ["target"]);
      }
    }

    snapshot = await content(target + sibling);
    targetRef = ref(snapshot, "Target");
    await browser.page.evaluate(() =>
      document.querySelector("#target").remove(),
    );
    await assert.rejects(browser.act(targetRef, value), StaleRefError);
    assert.deepEqual(await events(), []);
  }
  console.log("browser identity regressions passed");
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
