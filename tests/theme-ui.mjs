import assert from "node:assert/strict";

export async function checkThemePreference(page) {
  const theme = () => page.locator("html").getAttribute("data-theme");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "dark",
  );
  assert.equal(await page.locator("#theme-select").inputValue(), "system");
  await page.locator("#theme-select").selectOption("light");
  assert.equal(await theme(), "light");
  await page.reload();
  assert.equal(await theme(), "light");
  assert.equal(await page.locator("#theme-select").inputValue(), "light");
  const other = await page.context().newPage();
  await other.goto("http://127.0.0.1:18088");
  await other.locator("#theme-select").selectOption("dark");
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "dark",
  );
  await page.emulateMedia({ colorScheme: "light" });
  assert.equal(await theme(), "dark");
  await other.close();
  await page.locator("#theme-select").selectOption("system");
  assert.equal(await theme(), "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "dark",
  );
  await page.screenshot({ path: "/tmp/mteam-dark-login.png", fullPage: true });
  await page.locator("#theme-select").selectOption("light");
  // Storage denial must not break initialization or theme switching.
  const restricted = await page
    .context()
    .browser()
    .newContext({ colorScheme: "dark" });
  try {
    await restricted.addInitScript(() => {
      Storage.prototype.getItem = Storage.prototype.setItem = () => {
        throw new DOMException("Blocked", "SecurityError");
      };
    });
    const isolated = await restricted.newPage();
    await isolated.goto("http://127.0.0.1:18088");
    assert.equal(
      await isolated.locator("html").getAttribute("data-theme"),
      "dark",
    );
    await isolated.locator("#theme-select").selectOption("light");
    assert.equal(
      await isolated.locator("html").getAttribute("data-theme"),
      "light",
    );
  } finally {
    await restricted.close();
  }
}

export async function checkDarkSurfaces(page, selectors) {
  for (const selector of selectors) {
    const colors = await page
      .locator(selector)
      .first()
      .evaluate((node) => {
        const style = getComputedStyle(node);
        let background = style.backgroundColor;
        let parent = node;
        while (background === "rgba(0, 0, 0, 0)" && parent.parentElement) {
          parent = parent.parentElement;
          background = getComputedStyle(parent).backgroundColor;
        }
        return { foreground: style.color, background };
      });
    const luminance = (rgb) => {
      const values = rgb
        .match(/[\d.]+/g)
        .slice(0, 3)
        .map(Number)
        .map((x) => {
          const value = x / 255;
          return value <= 0.04045
            ? value / 12.92
            : ((value + 0.055) / 1.055) ** 2.4;
        });
      return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
    };
    const a = luminance(colors.foreground),
      b = luminance(colors.background);
    assert.ok(
      (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) >= 4.5,
      `${selector}: insufficient text contrast`,
    );
  }
}
