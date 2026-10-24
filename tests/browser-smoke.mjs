// Optional E2E test against tests.smoke_app, NOT against a real installation.
import assert from "node:assert/strict";
import { checkJobsWorkspace } from "./jobs-ui.mjs";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROMIUM_PATH
    ? { executablePath: process.env.CHROMIUM_PATH }
    : {}),
});
const page = await browser.newPage({
  viewport: { width: 1280, height: 900 },
  permissions: ["clipboard-read", "clipboard-write"],
  locale: "zh-CN",
  timezoneId: "Asia/Hong_Kong",
});
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
  await page.locator("#setup-guide").waitFor({ state: "visible" });
  await page.locator("#setup-open").click();
  await page
    .locator("#settings-form [name=site_api_key]")
    .fill("test-only-site-key");
  await page
    .locator("#settings-form [name=qb_url]")
    .fill("http://qb.test:8080");
  await page
    .locator("#settings-form [name=qb_password]")
    .fill("test-only-qb-password");
  await page.locator("#settings-dirty").waitFor({ state: "visible" });
  assert.equal(await page.locator("#test-qb").isDisabled(), true);
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.locator("[data-tab=search]").click();
  assert.equal(await page.locator("#settings-panel").isVisible(), true);
  await page.locator("#settings-form button[type=submit]").click();
  await page
    .getByRole("status")
    .filter({ hasText: "连接设置已保存" })
    .waitFor();
  await page.locator("#settings-dirty").waitFor({ state: "hidden" });
  await page.locator("#setup-guide").waitFor({ state: "hidden" });
  await page.locator("#test-qb").click();
  await page
    .locator("#connection-result")
    .filter({ hasText: "v5-test" })
    .waitFor();
  await page.locator("[data-tab=search]").click();
  await page.locator("#search-form [name=keyword]").fill("测试资源");
  await page.route("**/api/v1/torrents/search?**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 350));
    await route.continue();
  });
  await page.locator("#search-form button").click();
  await page.getByRole("button", { name: "搜索中…", exact: true }).waitFor();
  assert.equal(await page.locator("#next-page").isDisabled(), true);
  await page.locator("#results").getByText("浏览器测试资源 1080p").waitFor();
  assert.equal(
    await page.locator("#search-form [name=mode]").inputValue(),
    "normal",
  );
  await page.locator("#search-form [name=mode]").selectOption("adult");
  await page.waitForTimeout(1100); // Respect the site's one-second search cooldown.
  const [adultSearch] = await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes("/api/v1/torrents/search?"),
    ),
    page.locator("#search-form button").click(),
  ]);
  assert.equal(new URL(adultSearch.url()).searchParams.get("mode"), "adult");
  assert.equal(adultSearch.status(), 200);
  await page.locator("#results").getByText("成人分区测试资源 1080p").waitFor();
  await page.waitForTimeout(1100);
  const [adultPage] = await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes("/api/v1/torrents/search?"),
    ),
    page.locator("#next-page").click(),
  ]);
  assert.equal(new URL(adultPage.url()).searchParams.get("mode"), "adult");
  assert.equal(new URL(adultPage.url()).searchParams.get("page"), "2");
  assert.equal(adultPage.status(), 200);
  await page.locator("#page-label").getByText("第 2 页").waitFor();
  await page.locator("#search-form [name=mode]").selectOption("normal");
  const [normalSearch] = await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes("/api/v1/torrents/search?"),
    ),
    page.locator("#search-form button").click(),
  ]);
  assert.equal(new URL(normalSearch.url()).searchParams.get("mode"), "normal");
  assert.equal(new URL(normalSearch.url()).searchParams.get("page"), "1");
  assert.equal(normalSearch.status(), 200);
  await page.locator("#results").getByText("浏览器测试资源 1080p").waitFor();
  await page.route("**/api/v1/torrents/search?**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: { message: "测试搜索故障" } }),
    }),
  );
  await page.locator("#search-form button").click();
  await page
    .locator("#search-feedback")
    .filter({ hasText: "测试搜索故障" })
    .waitFor();
  assert.equal(await page.locator("#results tr").count(), 1);
  assert.equal(await page.locator("#search-form button").isEnabled(), true);
  await page.unroute("**/api/v1/torrents/search?**");
  await page.locator("#search-form button").click();
  await page
    .locator("#search-feedback")
    .filter({ hasText: "本页 1 条" })
    .waitFor();
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
  const submissionKeys = [];
  await page.route("**/api/v1/downloads", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    submissionKeys.push(route.request().headers()["idempotency-key"]);
    if (submissionKeys.length === 1) {
      // The server accepted the first request, but the response was lost.
      await route.fetch();
      return route.abort("failed");
    }
    return route.continue();
  });
  await page.locator("#download-form button[type=submit]").click();
  await page
    .getByRole("button", { name: "重试本次提交", exact: true })
    .waitFor();
  assert.equal(await page.locator("#download-dialog").isVisible(), true);
  await page.locator("#download-form button[type=submit]").click();
  await page.locator("#jobs-panel").waitFor({ state: "visible" });
  assert.equal(submissionKeys.length, 2);
  assert.equal(submissionKeys[0], submissionKeys[1]);
  await page.unroute("**/api/v1/downloads");
  await page.locator("#jobs-panel").waitFor({ state: "visible" });
  await page.waitForFunction(() =>
    document.querySelector("#jobs").textContent.includes("已添加"),
  );
  await page.locator(".highlighted-job").waitFor({ state: "visible" });
  assert.match(
    await page.locator("#jobs-refresh-state").textContent(),
    /最后更新/,
  );
  await page.route("**/api/v1/downloads?**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: { message: "测试刷新故障" } }),
    }),
  );
  await page.locator("#refresh-jobs").click();
  await page
    .locator("#jobs-refresh-state")
    .filter({ hasText: "测试刷新故障" })
    .waitFor();
  assert.equal(await page.locator("#jobs tr").count(), 1);
  await page.unroute("**/api/v1/downloads?**");
  await page.locator("#refresh-jobs").click();
  await page
    .locator("#jobs-refresh-state")
    .filter({ hasText: "最后更新" })
    .waitFor();
  await page.locator("#jobs progress").waitFor({ state: "visible" });
  assert.equal(
    await page.locator("#jobs progress").evaluate((node) => node.value),
    0.5,
  );
  assert.match(await page.locator("#jobs").textContent(), /50\.0%/);
  assert.match(await page.locator("#jobs").textContent(), /1\.00 MiB\/s/);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({
    path: "/tmp/mteam-downloader-jobs-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({
    path: "/tmp/mteam-downloader-jobs.png",
    fullPage: true,
  });
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
  await page.locator("#copy-token").click();
  await page.locator("#copy-feedback").filter({ hasText: "已复制" }).waitFor();
  assert.equal(
    await page.evaluate(() => navigator.clipboard.readText()),
    token,
  );
  const result = await page.request.get(
    "http://127.0.0.1:18088/api/v1/download-locations",
    { headers: { Authorization: "Bearer " + token } },
  );
  assert.equal(result.status(), 200);
  assert.equal((await result.json()).items[0].path, "/downloads");
  page.once("dialog", (dialog) => dialog.accept());
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
  await page.locator("[data-tab=jobs]").click();
  const beforeDelete = await page.request.get(
    "http://127.0.0.1:18088/api/v1/downloads",
  );
  const jobId = (await beforeDelete.json()).items[0].id;
  await page.locator("#jobs button").first().click();
  await page
    .locator("#job-detail .drawer-delete")
    .waitFor({ state: "visible" });
  page.once("dialog", (dialog) => dialog.dismiss());
  await page
    .locator("#job-detail")
    .getByRole("button", { name: "删除提交记录", exact: true })
    .click();
  assert.equal(await page.locator("#jobs tr").count(), 1);
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .locator("#job-detail")
    .getByRole("button", { name: "删除提交记录", exact: true })
    .click();
  await page.waitForFunction(() =>
    document.querySelector("#jobs-count").textContent.includes("共 0 条记录"),
  );
  assert.equal(await page.locator("#jobs tr").count(), 0);
  assert.equal(await page.locator("#jobs-empty").isVisible(), true);
  const deleted = await page.request.get(
    "http://127.0.0.1:18088/api/v1/downloads/" + jobId,
  );
  assert.equal(deleted.status(), 404);
  // Re-submission under a new logical action finds the still-existing qBittorrent task.
  const repeated = await page.request.post(
    "http://127.0.0.1:18088/api/v1/downloads",
    {
      headers: {
        Origin: "http://127.0.0.1:18088",
        "Idempotency-Key": "browser-after-record-deletion",
      },
      data: { torrent_id: "mteam:123456" },
    },
  );
  assert.equal(repeated.status(), 202);
  const repeatId = (await repeated.json()).id;
  let repeatStatus;
  for (let attempt = 0; attempt < 30; attempt++) {
    repeatStatus = await (
      await page.request.get(
        "http://127.0.0.1:18088/api/v1/downloads/" +
          repeatId +
          "?include_live=false",
      )
    ).json();
    if (repeatStatus.submission_status === "exists") break;
    await page.waitForTimeout(200);
  }
  assert.equal(repeatStatus.submission_status, "exists");
  await checkJobsWorkspace(page, repeatStatus);
  await page.locator("#logout").click();
  await page.locator("#login-panel").waitFor({ state: "visible" });
  assert.deepEqual(errors, []);
  console.log(
    "Browser smoke passed: setup guide → unsaved changes → search/loading/error recovery → idempotent retry after lost response → highlighted job/live refresh recovery → responsive cards → token copy/revoke → delete record/empty state → workspace filters/pagination/drawer/recheck/history recovery → logout",
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
