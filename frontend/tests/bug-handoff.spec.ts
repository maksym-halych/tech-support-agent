import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const controlURL = "http://127.0.0.1:2027";
const headers = {
  Authorization: "Bearer phase6-test-control-only-not-production",
};
const question = "Which grade did you select?";
const handoff = "Your report has been passed to the team for review.";
const forbidden = [
  "PRIVATE-DIAGNOSIS-DO-NOT-EXPOSE",
  "test-only-worker-token",
  "approved_by",
  "private_summary",
  "wait_for_team",
  "CaseSnapshot",
];

async function status(request: APIRequestContext) {
  const response = await request.get(`${controlURL}/status`, { headers });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function openChat(page: Page, request: APIRequestContext) {
  const state = await status(request);
  await page.goto(
    `/?assistantId=bug_demo&apiUrl=${encodeURIComponent(state.api_url)}`,
  );
  await expect(page.locator("textarea").last()).toBeVisible();
  expect(
    page.url(),
    "Expired/mismatched Clerk session must fail, never bypass",
  ).not.toContain("sign-in");
}

async function submit(page: Page, text: string) {
  const composer = page.locator("textarea").last();
  await composer.fill(text);
  await composer.press("Enter");
}

test.beforeEach(async ({ request }) => {
  const response = await request.post(`${controlURL}/reset`, {
    headers,
    data: {},
  });
  expect(response.ok()).toBeTruthy();
});

test("submit, reload, edit without ID, regenerate, clarify and public privacy", async ({
  page,
  request,
}) => {
  await openChat(page, request);
  const api = (await status(request)).api_url;
  const bodies: Promise<string>[] = [];
  page.on("response", (response) => {
    if (response.url().startsWith(api))
      bodies.push(response.text().catch(() => ""));
  });
  await submit(page, "Continue remains disabled after choosing a grade");
  await expect(page.getByText(question, { exact: true })).toBeVisible();
  const url = page.url();
  await page.reload();
  await expect(page.getByText(question, { exact: true })).toBeVisible();
  expect(page.url()).toBe(url);
  await page.getByRole("button", { name: "Edit", exact: true }).first().click();
  await page
    .locator("textarea")
    .first()
    .fill("Edited: Continue stays disabled");
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect(page.getByText(question, { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Refresh", exact: true })
    .last()
    .click();
  await expect(page.getByText(question, { exact: true })).toBeVisible();
  expect((await status(request)).cases).toBe(1);
  await submit(page, "Grade 3");
  await expect(page.getByText(handoff, { exact: true })).toBeVisible();
  await expect.poll(async () => (await status(request)).active).toBe(false);
  expect((await status(request)).trace).toEqual([
    "investigate",
    "investigate",
    "team_report",
  ]);
  const thread = new URL(page.url()).searchParams.get("threadId");
  expect(thread).toBeTruthy();
  const state = await request.get(`${api}/threads/${thread}/state`);
  const history = await request.post(`${api}/threads/${thread}/history`, {
    data: {},
  });
  expect(state.ok()).toBeTruthy();
  expect(history.ok()).toBeTruthy();
  const payloads = [
    ...(await Promise.all(bodies)),
    await state.text(),
    await history.text(),
    await page.locator("body").innerText(),
  ];
  for (const payload of payloads) {
    for (const privateValue of forbidden)
      expect(payload).not.toContain(privateValue);
  }
});

test("second thread is busy while clarification owns the slot", async ({
  page,
  request,
}) => {
  await openChat(page, request);
  await submit(page, "Continue bug");
  await expect(page.getByText(question, { exact: true })).toBeVisible();
  await openChat(page, request);
  await submit(page, "Another bug in a new thread");
  await expect(page.getByText(/another report|busy/i)).toBeVisible();
  expect((await status(request)).cases).toBe(1);
});

test("unavailable worker does not claim a handoff", async ({
  page,
  request,
}) => {
  await request.post(`${controlURL}/stop-worker`, { headers });
  await openChat(page, request);
  await submit(page, "Continue bug");
  await expect(
    page.getByText(
      "I can't pass your report to the team right now. Please try again later.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByText(handoff, { exact: true })).toHaveCount(0);
});
