// Optional E2E test against tests.smoke_app, NOT against a real installation.
import assert from "node:assert/strict";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  await page.goto("http://127.0.0.1:18088");
  await page.locator("#login-panel").waitFor({ state: "visible" });
  await page
    .locator("#login-form [name=password]")
    .fill("local-test-password-123");
  await page.locator("#login-form button").click();
  await page.locator("#application").waitFor({ state: "visible" });
  await page.locator("[data-tab=settings]").click();
  await page
    .locator("#settings-form [name=site_api_key]")
    .fill("test-only-site-key");
  await page
    .locator("#settings-form [name=qb_url]")
    .fill("http://qb.test:8080");
  await page
    .locator("#settings-form [name=qb_password]")
    .fill("test-only-qb-password");
  await page.locator("#settings-form button[type=submit]").click();
  await page
    .getByRole("status")
    .filter({ hasText: "连接设置已保存" })
    .waitFor();
  await page.locator("#test-qb").click();
  await page
    .locator("#connection-result")
    .filter({ hasText: "v5-test" })
    .waitFor();
  await page.locator("[data-tab=search]").click();
  await page.locator("#search-form [name=keyword]").fill("测试资源");
  await page.locator("#search-form button").click();
  await page.locator("#results").getByText("浏览器测试资源 1080p").waitFor();
  await page.screenshot({
    path: "/tmp/mteam-downloader-search.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({
    path: "/tmp/mteam-downloader-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.locator("#results button").click();
  await page.locator("#download-dialog").waitFor({ state: "visible" });
  await page.locator("#download-form button[type=submit]").click();
  await page.locator("#jobs-panel").waitFor({ state: "visible" });
  await page.waitForFunction(() =>
    document.querySelector("#jobs").textContent.includes("已添加"),
  );
  await page.locator("#jobs button").first().click();
  await page
    .locator("#job-detail")
    .filter({ hasText: "downloading" })
    .waitFor();
  await page.locator("[data-tab=tokens]").click();
  await page.locator("#token-form [name=name]").fill("Browser AI");
  await page.locator('#token-form [value="downloads:create"]').check();
  await page.locator("#token-form button").click();
  await page.locator("#new-token").filter({ hasText: "mtd_" }).waitFor();
  const token = (await page.locator("#new-token").textContent())
    .split("\n")
    .at(-1);
  const result = await page.request.get(
    "http://127.0.0.1:18088/api/v1/download-locations",
    { headers: { Authorization: "Bearer " + token } },
  );
  assert.equal(result.status(), 200);
  assert.equal((await result.json()).items[0].path, "/downloads");
  await page.locator("#token-list button").first().click();
  await page
    .locator("#token-list")
    .getByText(/已撤销/)
    .waitFor();
  const revoked = await page.request.get(
    "http://127.0.0.1:18088/api/v1/download-locations",
    { headers: { Authorization: "Bearer " + token } },
  );
  assert.equal(revoked.status(), 401);
  await page.locator("#logout").click();
  await page.locator("#login-panel").waitFor({ state: "visible" });
  assert.deepEqual(errors, []);
  console.log(
    "Browser smoke passed: login → settings → search → submit → query → API token → revoke → logout",
  );
} catch (error) {
  console.error("UI notice:", await page.locator("#notice").textContent());
  console.error("Page errors:", errors);
  await page.screenshot({
    path: "/tmp/mteam-downloader-failure.png",
    fullPage: true,
  });
  throw error;
} finally {
  await browser.close();
}
