// Additional workspace checks use mocked API responses, never real downloads.
import assert from "node:assert/strict";
import { checkDarkSurfaces } from "./theme-ui.mjs";
export async function checkJobsWorkspace(page, base) {
  const now = Date.now() / 1000;
  const live = {
    name: "Interstellar.2014.2160p.UHD.BluRay",
    state: "downloading",
    progress: 0.5,
    dlspeed: 1048576,
    amount_left: 536870912,
    save_path: "/downloads",
  };
  const make = (id, patch = {}) => ({
    ...base,
    id: "ui-" + id,
    created_at: now,
    updated_at: now,
    submission_status: "added",
    message: "已确认添加到 qBittorrent",
    actual_save_path: "/downloads",
    save_path: "/downloads",
    live_error: null,
    download_status: { ...live },
    ...patch,
  });
  const records = [
    make("uncertain", {
      submission_status: "uncertain",
      download_status: null,
      message: "请求超时，尚未确认是否已添加",
    }),
    make("failed", {
      submission_status: "failed",
      download_status: null,
      message: "站点连接失败",
    }),
    make("missing", {
      download_status: { ...live, progress: 1, state: "missingFiles" },
    }),
    make("mismatch", {
      download_status: { ...live, save_path: "/different-path" },
    }),
    make("unavailable", {
      download_status: null,
      live_error: { code: "DOWNLOADER_UNREACHABLE", message: "无法连接下载器" },
    }),
    make("unknown", { download_status: { ...live, progress: null } }),
    make("queued", { submission_status: "queued", download_status: null }),
    make("active"),
    make("paused", {
      download_status: { ...live, state: "stoppedDL", progress: 0.25 },
    }),
    ...Array.from({ length: 5 }, (_, i) =>
      make("complete-" + i, {
        download_status: {
          ...live,
          name: "Completed.resource." + i,
          state: "uploading",
          progress: 1,
        },
      }),
    ),
  ];
  let reconciled = false;
  const requests = [];
  const observe = (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v1/downloads"
    )
      requests.push(request);
  };
  page.on("request", observe);
  await page.route("**/api/v1/downloads?**", (route) =>
    route.fulfill({ json: { items: records, total: records.length } }),
  );
  await page.route("**/api/v1/downloads/ui-*", async (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1);
    if (id === "ui-failed")
      await new Promise((resolve) => setTimeout(resolve, 500));
    let detail = records.find((item) => item.id === id);
    if (id === "ui-uncertain" && reconciled)
      detail = { ...detail, submission_status: "added", download_status: live };
    await route.fulfill({ json: detail });
  });
  await page.locator("#refresh-jobs").click();
  await page
    .locator("#jobs-count")
    .filter({ hasText: "共 14 条记录" })
    .waitFor();
  const count = async (filter) =>
    Number(
      await page.locator(`[data-job-filter=${filter}] span`).textContent(),
    );
  assert.equal(await count("attention"), 6);
  assert.equal(await count("active"), 3);
  assert.equal(await count("completed"), 5);
  assert.equal(await page.locator("#jobs tr").count(), 10);
  await page.locator("#jobs-next").click();
  assert.equal(await page.locator("#jobs tr").count(), 4);
  await page.locator('[data-job-filter="completed"]').click();
  assert.equal(await page.locator("#jobs tr").count(), 5);
  assert.equal(await page.locator("#jobs-next").isDisabled(), true);
  await page.locator('[data-job-filter="attention"]').click();
  assert.equal(await page.locator("#jobs tr").count(), 6);
  await page.locator('[data-job-id="ui-failed"] button').click();
  await page.locator('[data-job-id="ui-uncertain"] button').click();
  await page
    .locator("#job-detail .job-warning")
    .filter({ hasText: "提交结果待确认" })
    .waitFor();
  await page.waitForTimeout(600); // Let the stale response arrive after selection changes.
  assert.match(await page.locator("#job-detail").textContent(), /ui-uncertain/);
  assert.equal(
    await page
      .locator("#jobs-workspace")
      .evaluate((node) => node.classList.contains("has-detail")),
    true,
  );
  await page.locator("#theme-select").selectOption("dark");
  await checkDarkSurfaces(page, [
    "#jobs-main",
    "#job-detail",
    ".job-warning",
    "#jobs-scope",
    ".status-uncertain",
    "#job-detail .drawer-refresh",
    ".filter-tabs button[aria-pressed=true]",
  ]);
  await page.screenshot({
    path: "/tmp/mteam-dark-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(
    () => document.querySelector("#job-mobile-dialog").open,
  );
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.locator("#job-detail .drawer-close").focus();
  await page.keyboard.press("Shift+Tab");
  assert.equal(
    await page.evaluate(() =>
      document
        .querySelector("#job-mobile-dialog")
        .contains(document.activeElement),
    ),
    true,
  );
  await page.locator("#job-detail .drawer-close").focus();
  await page.locator("#job-mobile-dialog").evaluate((node) => {
    node.scrollTop = 0;
  });
  const mobileBounds = await page.locator("#job-mobile-dialog").boundingBox();
  assert.equal(mobileBounds.x, 0);
  assert.equal(mobileBounds.y, 0);
  assert.equal(mobileBounds.width, 390);
  assert.equal(mobileBounds.height, 844);
  await page.screenshot({
    path: "/tmp/mteam-dark-mobile-detail.png",
    fullPage: false,
  });
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#job-detail").isVisible(), false);
  assert.equal(
    await page
      .locator('[data-job-id="ui-uncertain"] button')
      .evaluate((node) => node === document.activeElement),
    true,
  );
  await page.screenshot({
    path: "/tmp/mteam-v2-mobile-list.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.locator('[data-job-id="ui-uncertain"] button').click();
  await page
    .getByRole("button", { name: "重新核对下载器", exact: true })
    .waitFor();
  reconciled = true;
  records[0] = {
    ...records[0],
    submission_status: "added",
    download_status: live,
  };
  await page
    .getByRole("button", { name: "重新核对下载器", exact: true })
    .click();
  await page
    .getByRole("button", { name: "刷新任务详情", exact: true })
    .waitFor();
  assert.equal(await count("attention"), 5);
  assert.equal(await count("active"), 4);
  assert.equal(requests.length, 0);
  await page.locator("#job-detail .drawer-close").click();
  // Changes invalidate previous test results instead of reporting them as current.
  await page.locator('[data-tab="settings"]').click();
  await checkDarkSurfaces(page, [
    "#settings-form input",
    "#settings-panel .card",
  ]);
  await page
    .locator('#settings-form [name="download_tag"]')
    .fill("workspace-test-tag");
  await page.locator('#settings-form button[type="submit"]').click();
  await page.locator("#settings-dirty").waitFor({ state: "hidden" });
  await page.locator('[data-tab="jobs"]').click();
  assert.match(
    await page.locator("#service-status").textContent(),
    /配置已修改，需重测/,
  );
  await page.unroute("**/api/v1/downloads?**");
  const history = Array.from({ length: 114 }, (_, i) => make("history-" + i));
  let failOlder = true;
  await page.route("**/api/v1/downloads?**", (route) => {
    const offset = Number(
      new URL(route.request().url()).searchParams.get("offset"),
    );
    if (offset === 100 && failOlder)
      return route.fulfill({
        status: 503,
        json: { error: { message: "历史加载失败" } },
      });
    return route.fulfill({
      json: {
        items: history.slice(offset, offset + 100),
        total: history.length,
      },
    });
  });
  await page.locator('[data-job-filter="all"]').click();
  await page.locator("#refresh-jobs").click();
  await page
    .locator("#jobs-scope")
    .filter({ hasText: "第 1–100 条" })
    .waitFor();
  await page.locator("#jobs-older").click();
  await page
    .locator("#jobs-refresh-state")
    .filter({ hasText: "历史加载失败" })
    .waitFor();
  assert.match(await page.locator("#jobs-scope").textContent(), /第 1–100 条/);
  failOlder = false;
  await page.locator("#jobs-older").click();
  await page
    .locator("#jobs-scope")
    .filter({ hasText: "第 101–114 条" })
    .waitFor();
  assert.equal(await page.locator("#jobs-older").isDisabled(), true);
  await page.locator("#jobs-newer").click();
  await page
    .locator("#jobs-scope")
    .filter({ hasText: "第 1–100 条" })
    .waitFor();
  assert.equal(await page.locator("#jobs-newer").isDisabled(), true);
  page.off("request", observe);
}
