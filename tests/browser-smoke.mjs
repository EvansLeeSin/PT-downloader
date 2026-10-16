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
  await page.setViewportSize({ width: 1280, height: 900 });
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
  await page.locator("[data-tab=jobs]").click();
  const beforeDelete = await page.request.get(
    "http://127.0.0.1:18088/api/v1/downloads",
  );
  const jobId = (await beforeDelete.json()).items[0].id;
  page.once("dialog", (dialog) => dialog.dismiss());
  await page
    .locator("#jobs")
    .getByRole("button", { name: "删除记录", exact: true })
    .click();
  assert.equal(await page.locator("#jobs tr").count(), 1);
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .locator("#jobs")
    .getByRole("button", { name: "删除记录", exact: true })
    .click();
  await page.waitForFunction(() =>
    document.querySelector("#jobs-count").textContent.includes("共 0 条记录"),
  );
  assert.equal(await page.locator("#jobs tr").count(), 0);
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
  await page.locator("#logout").click();
  await page.locator("#login-panel").waitFor({ state: "visible" });
  assert.deepEqual(errors, []);
  console.log(
    "Browser smoke passed: login → settings → normal/adult search + pagination → submit → live progress → API token → revoke → delete record (torrent retained) → logout",
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
