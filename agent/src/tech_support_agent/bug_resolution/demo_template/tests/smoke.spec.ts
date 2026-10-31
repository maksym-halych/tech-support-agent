import { test, expect } from "@playwright/test";
import { summarySubjects } from "../src/subjects";

test("subject selection reaches the grade screen and supports going back", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "What sparks your curiosity?" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Math Numbers" }).click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Grade", { exact: true }).selectOption("3");
  await expect(page.getByLabel("Grade", { exact: true })).toHaveValue("3");
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Math Numbers" }),
  ).toHaveAttribute("aria-pressed", "true");
});

test("single subject summaries retain the selected subject", () => {
  expect(summarySubjects("Math")).toEqual(["Math"]);
  expect(summarySubjects("Reading")).toEqual(["Reading"]);
});

test("small viewport keeps the choices and demo notice visible", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Both Math" })).toBeVisible();
  await expect(
    page.getByText("Just a demo. Your choices stay in this tab."),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
