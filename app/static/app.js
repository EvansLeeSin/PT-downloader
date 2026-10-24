"use strict";
const $ = (selector) => document.querySelector(selector);
// getRandomValues also works on LAN HTTP; randomUUID requires a secure context.
const uniqueId = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
let searchState = null;
let page = 1;
let selection = null;
let noticeTimer;
let searchLoading = false;
let highlightedJobId = null;
let pendingHighlight = false;
let settingsSnapshot = null;
let locationsSnapshot = null;
let lastJobsUpdate = null;
let tokenValue = "";
let settingsRevision = 0;
let sessionGeneration = 0;
const scopeNames = {
  "search:read": "搜索资源",
  "downloads:read": "目录与状态查询",
  "downloads:create": "添加下载",
  "downloads:delete": "删除提交记录",
};
function settingsDirty() {
  return (
    settingsSnapshot !== null &&
    settingsSnapshot !== JSON.stringify([...new FormData($("#settings-form"))])
  );
}
function locationFingerprint() {
  return JSON.stringify(
    [...document.querySelectorAll(".location-row input")].map((input) => [
      input.name,
      input.value,
      input.checked,
    ]),
  );
}
function locationsDirty() {
  return (
    locationsSnapshot !== null && locationsSnapshot !== locationFingerprint()
  );
}
function updateDirty() {
  $("#settings-dirty").hidden = !settingsDirty();
  for (const id of ["#test-mteam", "#test-qb"])
    $(id).disabled = settingsDirty();
}
$("#settings-form").addEventListener("input", updateDirty);
window.addEventListener("beforeunload", (event) => {
  if (settingsDirty() || locationsDirty()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
function technicalDetails(data) {
  const details = element("details");
  details.append(
    element("summary", "技术详情"),
    element("pre", JSON.stringify(data, null, 2)),
  );
  return details;
}
async function loadSetupGuide() {
  const data = await api("/admin/settings");
  const site = Boolean(data.site_api_key_configured);
  const qb = Boolean(
    data.qb_url && data.qb_username && data.qb_password_configured,
  );
  $("#setup-guide").hidden = site && qb;
  $("#setup-steps").replaceChildren(
    element("li", `${site ? "已保存" : "待配置"} · M-Team API 地址与密钥`),
    element("li", `${qb ? "已保存" : "待配置"} · qBittorrent WebUI 连接`),
    element("li", "确认保存目录 · 使用下载器容器内路径"),
  );
}
$("#setup-open").addEventListener("click", () =>
  busy($("#setup-open"), () => tab("settings")),
);
$("#jobs-search").addEventListener("click", () => tab("search"));
const statusNames = {
  queued: "排队中",
  processing: "处理中",
  added: "已添加",
  exists: "已存在",
  failed: "失败",
  uncertain: "待确认",
};
function notice(message, error = false) {
  clearTimeout(noticeTimer);
  const box = $("#notice");
  box.textContent = message;
  box.classList.toggle("error", error);
  box.hidden = false;
  if (!error)
    noticeTimer = setTimeout(() => {
      box.hidden = true;
    }, 7000);
}
async function api(path, options = {}) {
  const response = await fetch("/api/v1" + path, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(data?.error?.message || `请求失败 (${response.status})`);
  }
  return data;
}
function showLogin() {
  sessionGeneration++;
  settingsRevision++;
  jobsData = { items: [], total: 0 };
  jobsOffset = 0;
  jobsPage = 1;
  jobsFilter = "all";
  lastJobsUpdate = null;
  closeJobDetail(false);
  connectionTests.mteam = connectionTests.qbittorrent = null;
  renderServiceStatus();
  settingsSnapshot = locationsSnapshot = null;
  if ($("#download-dialog").open) $("#download-dialog").close();
  $("#new-token").textContent = "";
  tokenValue = "";
  $("#token-reveal").hidden = true;
  $("#new-token").hidden = true;
  $("#application").hidden = true;
  $("#login-panel").hidden = false;
}
function showApplication() {
  $("#login-panel").hidden = true;
  $("#application").hidden = false;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function button(text, action, className = "secondary") {
  const node = element("button", text, className);
  node.type = "button";
  node.addEventListener("click", () => busy(node, action));
  return node;
}
async function busy(node, action) {
  node.disabled = true;
  try {
    await action();
  } catch (err) {
    notice(err.message, true);
  } finally {
    node.disabled = false;
  }
}
function formAction(selector, action) {
  $(selector).addEventListener("submit", (event) => {
    event.preventDefault();
    busy(event.submitter, () => action(event.target));
  });
}
function size(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes,
    index = 0;
  while (value >= 1024 && index < 4) {
    value /= 1024;
    index++;
  }
  return `${value.toFixed(index ? 2 : 0)} ${units[index]}`;
}
function date(seconds) {
  return new Date(seconds * 1000).toLocaleString();
}
async function tab(name) {
  if (
    !$("#settings-panel").hidden &&
    name !== "settings" &&
    (settingsDirty() || locationsDirty())
  ) {
    if (!confirm("连接或目录有未保存的修改，离开将放弃这些修改。继续？"))
      return;
    settingsSnapshot = null;
    locationsSnapshot = null;
  }
  if (
    name === "settings" &&
    !$("#settings-panel").hidden &&
    (settingsDirty() || locationsDirty())
  )
    return;
  if (name !== "jobs" && selectedJobId) closeJobDetail(false);
  document.querySelectorAll(".tab-panel").forEach((node) => {
    node.hidden = node.id !== name + "-panel";
  });
  document
    .querySelectorAll("[data-tab]")
    .forEach((node) =>
      node.classList.toggle("active", node.dataset.tab === name),
    );
  if (name === "settings") await loadSettings();
  if (name === "jobs") await loadJobs();
  if (name === "tokens") await loadTokens();
}
document
  .querySelectorAll("[data-tab]")
  .forEach((node) =>
    node.addEventListener("click", () =>
      busy(node, () => tab(node.dataset.tab)),
    ),
  );
formAction("#login-form", async (form) => {
  await api("/auth/login", {
    method: "POST",
    body: JSON.stringify({ password: form.password.value }),
  });
  form.reset();
  showApplication();
  await loadSetupGuide();
});
$("#logout").addEventListener("click", () =>
  busy($("#logout"), async () => {
    if (
      (settingsDirty() || locationsDirty()) &&
      !confirm("有未保存的修改，确定退出登录？")
    )
      return;
    await api("/auth/logout", { method: "POST" });
    $("#new-token").textContent = "";
    tokenValue = "";
    $("#token-reveal").hidden = true;
    showLogin();
  }),
);
formAction("#search-form", async (form) => {
  if (searchLoading) return;
  const criteria = {
    keyword: form.keyword.value.trim(),
    mode: form.mode.value,
    free_only: form.free_only.checked,
  };
  await search(1, criteria);
});
async function search(targetPage = page, criteria = searchState) {
  if (searchLoading) return;
  searchLoading = true;
  const feedback = $("#search-feedback");
  const submit = $("#search-form button[type=submit]");
  const previousText = submit.textContent;
  submit.disabled = true;
  submit.textContent = "搜索中…";
  $("#results").setAttribute("aria-busy", "true");
  feedback.hidden = false;
  feedback.classList.remove("error-text");
  feedback.textContent = "正在搜索，下面的已有结果将在完成后更新…";
  $("#prev-page").disabled = $("#next-page").disabled = true;
  try {
    const params = new URLSearchParams({
      ...criteria,
      page: targetPage,
      page_size: 20,
    });
    const result = await api("/torrents/search?" + params);
    page = result.page;
    searchState = criteria;
    $("#results").replaceChildren();
    for (const item of result.items) {
      const row = element("tr");
      const title = element("td");
      const link = element("a", item.title);
      link.href = item.detail_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      title.append(link);
      if (item.description) {
        const description = element(
          "p",
          item.description,
          "muted resource-description",
        );
        description.title = item.description;
        title.append(description);
      }
      if (item.labels.length)
        title.append(element("p", item.labels.join(" · "), "muted"));
      const promo = item.promotion;
      const promoCell = element("td");
      const text =
        promo.type === "free"
          ? "免费"
          : promo.type === "discount"
            ? `${promo.download_factor * 100}% 计费`
            : promo.type === "normal"
              ? "普通"
              : "待核对";
      promoCell.append(
        element("span", text, "badge " + (promo.type === "free" ? "free" : "")),
      );
      if (promo.expires_at)
        promoCell.append(
          element(
            "p",
            "截至 " + new Date(promo.expires_at).toLocaleString(),
            "muted",
          ),
        );
      const action = element("td");
      action.append(button("下载", () => openDownload(item), "primary"));
      row.append(
        title,
        element("td", size(item.size_bytes)),
        element(
          "td",
          `${item.seeders} / ${item.leechers}${item.seeders === 0 ? " · 暂无做种" : ""}`,
        ),
        promoCell,
        action,
      );
      $("#results").append(row);
    }
    if (!result.items.length) {
      const row = element("tr");
      const cell = element(
        "td",
        "未找到资源。试试更短的关键词、切换综合 / 成人分区，或取消免费筛选。",
        "muted empty-state",
      );
      cell.colSpan = 5;
      row.append(cell);
      $("#results").append(row);
    }
    feedback.textContent = `“${searchState.keyword}” · ${searchState.mode === "adult" ? "成人" : "综合"}${searchState.free_only ? " · 免费筛选" : ""} · 本页 ${result.items.length} 条${Number.isFinite(result.total) ? ` / 共 ${result.total} 条` : ""}`;
    labelCells($("#results"));
    $("#page-label").textContent = `第 ${result.page} 页`;
    $("#prev-page").disabled = result.page <= 1;
    $("#next-page").disabled = !result.has_more;
  } catch (err) {
    feedback.textContent = `搜索失败：${err.message}。下方保留上次结果，请重试。`;
    feedback.classList.add("error-text");
    $("#prev-page").disabled = !searchState || page <= 1;
    $("#next-page").disabled = true;
  } finally {
    searchLoading = false;
    submit.disabled = false;
    submit.textContent = previousText;
    $("#results").removeAttribute("aria-busy");
  }
}
for (const [selector, delta] of [
  ["#prev-page", -1],
  ["#next-page", 1],
])
  $(selector).addEventListener("click", async () => {
    if (searchLoading) return;
    try {
      await search(page + delta);
    } catch (err) {
      notice(err.message, true);
    }
  });
async function openDownload(item) {
  const data = await api("/download-locations");
  const select = $("#download-form").location_id;
  select.replaceChildren();
  for (const location of data.items) {
    const option = element("option", `${location.name} — ${location.path}`);
    option.value = location.id;
    option.selected = location.is_default;
    select.append(option);
  }
  selection = { id: item.id, key: uniqueId() };
  $("#download-title").textContent = item.title;
  $("#download-summary").textContent =
    `${size(item.size_bytes)} · ${item.promotion.type === "free" ? "免费" : item.promotion.type === "discount" ? `${item.promotion.download_factor * 100}% 计费` : "优惠请以站点当前状态为准"} · ${item.seeders} 人做种`;
  $("#download-form button[type=submit]").textContent = "添加到 qBittorrent";
  $("#download-form").start_paused.checked = false;
  $("#download-dialog").showModal();
}
$("#cancel-download").addEventListener("click", () =>
  $("#download-dialog").close(),
);
formAction("#download-form", async (form) => {
  const body = {
    torrent_id: selection.id,
    location_id: form.location_id.value,
    start_paused: form.start_paused.checked,
  };
  // Bind a key to the exact payload. Keep it on transport failure for safe retry.
  const fingerprint = JSON.stringify(body);
  if (selection.fingerprint && selection.fingerprint !== fingerprint)
    selection.key = uniqueId();
  selection.fingerprint = fingerprint;
  const submit = form.querySelector("button[type=submit]");
  submit.textContent = "正在提交…";
  let result;
  const controls = [
    ...form.querySelectorAll("select, input, #cancel-download"),
  ];
  controls.forEach((node) => {
    node.disabled = true;
  });
  const preventClose = (event) => event.preventDefault();
  $("#download-dialog").addEventListener("cancel", preventClose);
  try {
    result = await api("/downloads", {
      method: "POST",
      headers: { "Idempotency-Key": selection.key },
      body: fingerprint,
    });
  } catch (err) {
    submit.textContent = "重试本次提交";
    throw err;
  } finally {
    controls.forEach((node) => {
      node.disabled = false;
    });
    $("#download-dialog").removeEventListener("cancel", preventClose);
  }
  if (jobsLoading) await jobsLoading;
  jobsOffset = 0;
  jobsPage = 1;
  highlightedJobId = result.id;
  pendingHighlight = true;
  $("#download-dialog").close();
  notice(result.message);
  await tab("jobs");
});
let jobsLoading = null;
function loadJobs() {
  // Slow downloader requests must not overlap on the five-second timer.
  if (!jobsLoading)
    jobsLoading = fetchJobs().finally(() => {
      jobsLoading = null;
    });
  return jobsLoading;
}
function downloadProgress(job) {
  const cell = element("td", undefined, "download-progress");
  const live = job.download_status;
  if (!live) {
    cell.append(element("span", job.live_error ? "进度暂不可用" : "—"));
    if (job.live_error)
      cell.append(element("p", job.live_error.message, "muted"));
    return cell;
  }
  const states = {
    downloading: "下载中",
    stalledDL: "等待下载",
    metaDL: "获取元数据",
    queuedDL: "等待下载",
    forcedDL: "强制下载中",
    allocating: "分配空间",
    pausedDL: "已暂停",
    stoppedDL: "已暂停",
    checkingDL: "校验中",
    uploading: "做种中",
    stalledUP: "做种中",
    forcedUP: "强制做种中",
    pausedUP: "已完成（暂停）",
    stoppedUP: "已完成（暂停）",
    queuedUP: "等待做种",
    checkingUP: "校验中",
    checkingResumeData: "检查恢复数据",
    moving: "移动文件中",
    error: "下载器错误",
    missingFiles: "文件缺失",
  };
  cell.append(element("span", states[live.state] || live.state || "状态未知"));
  if (Number.isFinite(live.progress)) {
    const fraction = Math.max(0, Math.min(1, live.progress));
    cell.append(element("span", ` · ${(fraction * 100).toFixed(1)}%`));
    const bar = element("progress");
    bar.max = 1;
    bar.value = fraction;
    bar.setAttribute("aria-label", "下载进度");
    cell.append(bar);
  }
  const summary = [];
  if (Number.isFinite(live.dlspeed)) summary.push(size(live.dlspeed) + "/s");
  if (Number.isFinite(live.amount_left))
    summary.push("剩余 " + size(live.amount_left));
  if (summary.length) cell.append(element("p", summary.join(" · "), "muted"));
  return cell;
}
const JOB_BATCH_SIZE = 100;
const JOB_PAGE_SIZE = 10;
let jobsData = { items: [], total: 0 };
let jobsOffset = 0;
let jobsPage = 1;
let jobsFilter = "all";
let selectedJobId = null;
let drawerRequest = 0;
let drawerReturnFocus = null;
const mobileDrawer = window.matchMedia("(max-width: 900px)");
const connectionTests = { mteam: null, qbittorrent: null };

function currentSavePath(job) {
  return job.download_status?.save_path || job.actual_save_path;
}
function jobGroup(job) {
  if (["failed", "uncertain"].includes(job.submission_status))
    return "attention";
  if (["queued", "processing"].includes(job.submission_status)) return "active";
  const live = job.download_status;
  if (
    job.live_error ||
    !live ||
    !Number.isFinite(live.progress) ||
    ["error", "missingFiles"].includes(live.state)
  )
    return "attention";
  if (
    currentSavePath(job) &&
    currentSavePath(job).replace(/\/+$/, "") !==
      job.save_path.replace(/\/+$/, "")
  )
    return "attention";
  return Number.isFinite(live.progress) && live.progress >= 1
    ? "completed"
    : "active";
}
function filterJobs(filter) {
  jobsFilter = filter;
  jobsPage = 1;
  renderJobs();
}
$("#job-filters").addEventListener("click", (event) => {
  const target = event.target.closest("[data-job-filter]");
  if (target) filterJobs(target.dataset.jobFilter);
});
$("#jobs-clear-filter").addEventListener("click", () => filterJobs("all"));
for (const [id, delta] of [
  ["#jobs-prev", -1],
  ["#jobs-next", 1],
]) {
  $(id).addEventListener("click", () => {
    jobsPage += delta;
    renderJobs();
  });
}
for (const [id, delta] of [
  ["#jobs-newer", -1],
  ["#jobs-older", 1],
]) {
  $(id).addEventListener("click", () =>
    busy($(id), async () => {
      if (jobsLoading) await jobsLoading;
      const previousOffset = jobsOffset;
      jobsOffset = Math.max(0, jobsOffset + delta * JOB_BATCH_SIZE);
      jobsPage = 1;
      closeJobDetail(false);
      if (!(await loadJobs())) jobsOffset = previousOffset;
    }).then(() => renderJobs()),
  );
}
async function fetchJobs() {
  const generation = sessionGeneration;
  const refresh = $("#jobs-refresh-state");
  try {
    let data = await api(
      `/downloads?limit=${JOB_BATCH_SIZE}&offset=${jobsOffset}&include_live=true`,
    );
    if (jobsOffset > 0 && !data.items.length) {
      const previousOffset = jobsOffset;
      const fallbackOffset = Math.max(
        0,
        Math.floor(Math.max(0, data.total - 1) / JOB_BATCH_SIZE) *
          JOB_BATCH_SIZE,
      );
      data = await api(
        `/downloads?limit=${JOB_BATCH_SIZE}&offset=${fallbackOffset}&include_live=true`,
      );
      // Commit the fallback only after the replacement request succeeds.
      if (jobsOffset === previousOffset) jobsOffset = fallbackOffset;
    }
    if (generation !== sessionGeneration) return false;
    jobsData = data;
    lastJobsUpdate = new Date().toLocaleTimeString();
    refresh.classList.remove("error-text");
    refresh.textContent = `最后更新 ${lastJobsUpdate} · 每 5 秒自动刷新`;
    if (pendingHighlight) {
      jobsFilter = "all";
      const index = data.items.findIndex((job) => job.id === highlightedJobId);
      if (index >= 0) jobsPage = Math.floor(index / JOB_PAGE_SIZE) + 1;
    }
    renderJobs();
    return true;
  } catch (err) {
    refresh.classList.add("error-text");
    refresh.textContent = `刷新失败：${err.message}。${lastJobsUpdate ? `当前为 ${lastJobsUpdate} 的结果。` : "尚无可显示的结果。"}点击“刷新”重试。`;
    return false;
  }
}
function renderJobs() {
  const focusedJob = $("#jobs").contains(document.activeElement)
    ? document.activeElement.closest("tr")?.dataset.jobId
    : null;
  const data = jobsData;
  const counts = {
    all: data.items.length,
    active: 0,
    attention: 0,
    completed: 0,
  };
  for (const job of data.items) counts[jobGroup(job)]++;
  for (const node of document.querySelectorAll("[data-job-filter]")) {
    node.setAttribute(
      "aria-pressed",
      String(node.dataset.jobFilter === jobsFilter),
    );
    node.querySelector("span").textContent = counts[node.dataset.jobFilter];
  }
  const filtered = data.items.filter(
    (job) => jobsFilter === "all" || jobGroup(job) === jobsFilter,
  );
  const pages = Math.max(1, Math.ceil(filtered.length / JOB_PAGE_SIZE));
  jobsPage = Math.max(1, Math.min(jobsPage, pages));
  const visible = filtered.slice(
    (jobsPage - 1) * JOB_PAGE_SIZE,
    jobsPage * JOB_PAGE_SIZE,
  );
  $("#jobs-empty").hidden = filtered.length > 0;
  $("#jobs-empty-text").textContent =
    data.total === 0 ? "还没有提交记录" : "当前范围内没有符合条件的记录";
  $("#jobs-search").hidden = data.total > 0;
  $("#jobs-clear-filter").hidden = jobsFilter === "all";
  $("#jobs-scope").textContent =
    data.total > JOB_BATCH_SIZE
      ? `筛选范围：全部 ${data.total} 条中的第 ${jobsOffset + 1}–${jobsOffset + data.items.length} 条。可在下方切换较新 / 更早记录。`
      : "筛选范围：全部提交记录。需关注包含提交失败、待确认、进度不可用及目录不一致。";
  $("#jobs-count").textContent =
    `共 ${data.total} 条记录 · 当前筛选 ${filtered.length} 条`;
  $("#jobs-page-label").textContent = `第 ${jobsPage} / ${pages} 页`;
  $("#jobs-prev").disabled = jobsPage <= 1;
  $("#jobs-next").disabled = jobsPage >= pages;
  $("#jobs-batches").hidden = data.total <= JOB_BATCH_SIZE;
  $("#jobs-newer").disabled = jobsOffset === 0;
  $("#jobs-older").disabled = jobsOffset + JOB_BATCH_SIZE >= data.total;
  $("#jobs").replaceChildren();
  for (const job of visible) {
    const row = element("tr");
    row.dataset.jobId = job.id;
    row.classList.toggle("highlighted-job", job.id === highlightedJobId);
    row.classList.toggle("selected-job", job.id === selectedJobId);
    const title = element("td");
    const name = element(
      "p",
      job.download_status?.name || job.torrent_id,
      "job-name",
    );
    name.title = name.textContent;
    title.append(name, element("p", date(job.created_at), "muted"));
    if (job.id === highlightedJobId)
      title.append(element("p", "本次提交", "current-job"));
    const path = element(
      "td",
      currentSavePath(job) || job.save_path,
      "job-path",
    );
    if (currentSavePath(job) && currentSavePath(job) !== job.save_path)
      path.append(element("p", "请求：" + job.save_path, "unsaved"));
    const state = element("td");
    state.append(
      element(
        "span",
        statusNames[job.submission_status] || job.submission_status,
        "badge status-" + job.submission_status,
      ),
    );
    if (jobGroup(job) === "attention")
      state.append(element("p", "需关注", "attention-label"));
    const action = element("td");
    const view = button("查看", () => openJobDetail(job.id));
    view.setAttribute("aria-label", `查看 ${name.textContent}`);
    view.setAttribute("aria-controls", "job-detail");
    action.append(view);
    row.append(title, path, state, downloadProgress(job), action);
    $("#jobs").append(row);
    if (job.id === highlightedJobId && pendingHighlight) {
      pendingHighlight = false;
      row.scrollIntoView({
        block: "nearest",
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
      });
    }
  }
  labelCells($("#jobs"));
  if (focusedJob) {
    const focusedRow = [...$("#jobs").rows].find(
      (node) => node.dataset.jobId === focusedJob,
    );
    (focusedRow?.querySelector("button") || $("#refresh-jobs")).focus({
      preventScroll: true,
    });
  }
}
function syncDrawerLayout() {
  const box = $("#job-detail");
  const dialog = $("#job-mobile-dialog");
  if (mobileDrawer.matches && selectedJobId) {
    if (box.parentElement !== dialog) dialog.append(box);
    if (!dialog.open) dialog.showModal();
    $("#jobs-workspace").classList.remove("has-detail");
  } else {
    if (dialog.open) dialog.close();
    if (box.parentElement !== $("#jobs-workspace"))
      $("#jobs-workspace").append(box);
    $("#jobs-workspace").classList.toggle("has-detail", Boolean(selectedJobId));
  }
}
mobileDrawer.addEventListener("change", syncDrawerLayout);
function closeJobDetail(restoreFocus = true) {
  selectedJobId = null;
  drawerRequest++;
  $("#job-detail").hidden = true;
  syncDrawerLayout();
  renderJobs();
  if (restoreFocus) {
    const row = [...$("#jobs").rows].find(
      (node) => node.dataset.jobId === drawerReturnFocus,
    );
    (row?.querySelector("button") || $("#refresh-jobs")).focus();
  }
}
$("#job-mobile-dialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  closeJobDetail();
});
document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape" &&
    selectedJobId &&
    !mobileDrawer.matches &&
    !$("#download-dialog").open
  )
    closeJobDetail();
});
function drawerHeader() {
  const header = element("div", undefined, "drawer-heading");
  const title = element("h2", "任务详情");
  title.id = "job-drawer-title";
  const close = button("×", () => closeJobDetail(), "secondary drawer-close");
  close.setAttribute("aria-label", "关闭任务详情");
  header.append(title, close);
  return header;
}
async function openJobDetail(id, focus = true) {
  const version = ++drawerRequest;
  drawerReturnFocus = id;
  selectedJobId = id;
  const box = $("#job-detail");
  box.replaceChildren(
    drawerHeader(),
    element("p", "正在查询任务详情…", "muted"),
  );
  box.hidden = false;
  box.setAttribute("aria-busy", "true");
  syncDrawerLayout();
  renderJobs();
  if (focus) box.querySelector("button").focus();
  try {
    const detail = await api("/downloads/" + encodeURIComponent(id));
    if (version !== drawerRequest) return;
    const index = jobsData.items.findIndex((job) => job.id === id);
    if (index >= 0) jobsData.items[index] = detail;
    renderJobDetail(detail);
    renderJobs();
    if (focus) box.querySelector(".drawer-close").focus();
    else box.querySelector(".drawer-refresh").focus();
  } catch (err) {
    if (version !== drawerRequest) return;
    box.replaceChildren(
      drawerHeader(),
      element("p", `查询失败：${err.message}`, "error-text"),
      button("重试查询", () => openJobDetail(id, false)),
    );
    box.querySelector("button:last-child").focus();
  } finally {
    if (version === drawerRequest) box.removeAttribute("aria-busy");
  }
}
function renderJobDetail(job) {
  const box = $("#job-detail");
  box.replaceChildren(
    drawerHeader(),
    element(
      "h3",
      job.download_status?.name || job.torrent_id,
      "drawer-resource",
    ),
    element("p", "提交 ID " + job.id, "muted"),
  );
  const needsAttention = jobGroup(job) === "attention";
  if (needsAttention) {
    const warning = element("div", undefined, "job-warning");
    warning.append(
      element(
        "strong",
        job.submission_status === "uncertain"
          ? "提交结果待确认"
          : "此任务需要关注",
      ),
      element("p", job.message),
    );
    if (job.submission_status === "uncertain")
      warning.append(
        element(
          "p",
          "先按 hash 核对下载器，避免重复提交。未找到任务不代表提交失败。",
        ),
      );
    if (job.live_error) warning.append(element("p", job.live_error.message));
    if (
      currentSavePath(job) &&
      currentSavePath(job).replace(/\/+$/, "") !==
        job.save_path.replace(/\/+$/, "")
    )
      warning.append(
        element("p", "实际保存目录与请求目录不一致，请检查下载器设置。"),
      );
    if (
      !["uncertain", "failed"].includes(job.submission_status) &&
      !job.live_error &&
      (!job.download_status || !Number.isFinite(job.download_status.progress))
    )
      warning.append(
        element("p", "下载进度暂不可用，请重新查询并检查下载器状态。"),
      );
    if (
      job.download_status &&
      ["error", "missingFiles"].includes(job.download_status.state)
    )
      warning.append(element("p", "请到 qBittorrent 检查文件与下载器状态。"));
    if (job.submission_status === "failed")
      warning.append(
        element("p", "请先检查连接设置与失败原因，本页不会自动重新提交。"),
      );
    box.append(warning);
  }
  box.append(
    button(
      job.submission_status === "uncertain" ? "重新核对下载器" : "刷新任务详情",
      () => openJobDetail(job.id, false),
      "primary drawer-refresh",
    ),
  );
  if (needsAttention)
    box.append(
      button("前往连接设置", () => {
        closeJobDetail(false);
        return tab("settings");
      }),
    );
  const facts = element("dl", undefined, "job-facts");
  for (const [label, value] of [
    ["提交状态", statusNames[job.submission_status]],
    ["请求目录", job.save_path],
    ["实际目录", currentSavePath(job) || "尚未获取"],
    ["初始状态", job.start_paused ? "添加后暂停" : "添加后开始"],
    ["提交时间", date(job.created_at)],
    ["记录更新", date(job.updated_at)],
    ["详情查询", new Date().toLocaleString()],
  ])
    facts.append(element("dt", label), element("dd", value));
  box.append(facts);
  if (job.download_status) {
    box.append(element("h3", "实际下载状态"));
    const progress = downloadProgress(job);
    const wrap = element("div", undefined, "drawer-progress");
    wrap.append(...progress.childNodes);
    box.append(wrap);
  }
  const timeline = element("ol", undefined, "job-timeline");
  timeline.append(
    element("li", `请求已接收 · ${date(job.created_at)}`),
    element(
      "li",
      `${statusNames[job.submission_status]} · 记录更新于 ${date(job.updated_at)}`,
    ),
  );
  box.append(
    element("h3", "处理记录"),
    timeline,
    element("p", "仅显示已记录的状态；详情为本次查询结果。", "muted"),
    technicalDetails(job),
  );
  const remove = button(
    "删除提交记录",
    () => deleteJobRecord(job),
    "secondary danger drawer-delete",
  );
  remove.disabled = ["queued", "processing"].includes(job.submission_status);
  if (remove.disabled) remove.title = "排队或提交中的记录暂不能删除";
  box.append(
    remove,
    element("p", "仅删除记录，不影响下载器任务或文件。", "muted"),
  );
}
async function deleteJobRecord(job) {
  if (
    !confirm(
      (job.submission_status === "uncertain"
        ? "该任务结果待确认，请先核对下载器。\n"
        : "") +
        "仅删除本应用的提交记录，不会删除 qBittorrent 任务或文件。确定删除？",
    )
  )
    return;
  await api("/downloads/" + encodeURIComponent(job.id), { method: "DELETE" });
  if (selectedJobId === job.id) closeJobDetail();
  if (jobsLoading) await jobsLoading;
  await loadJobs();
  notice("提交记录已删除，下载器任务和文件不受影响");
}
function renderServiceStatus() {
  $("#service-status").replaceChildren();
  for (const [service, name] of [
    ["mteam", "M-Team"],
    ["qbittorrent", "qBittorrent"],
  ]) {
    const test = connectionTests[service];
    const label = !test
      ? "未测试"
      : test.state === "stale"
        ? "配置已修改，需重测"
        : test.state === "testing"
          ? "正在测试…"
          : test.state === "success"
            ? "上次测试成功"
            : "上次测试失败";
    const pill = button("", () => tab("settings"), "secondary service-pill");
    pill.append(
      element("strong", name),
      element("span", label, `service-${test?.state || "unknown"}`),
    );
    if (test?.at) pill.append(element("span", test.at, "muted"));
    $("#service-status").append(pill);
  }
}
renderServiceStatus();
$("#refresh-jobs").addEventListener("click", () =>
  busy($("#refresh-jobs"), loadJobs),
);
setInterval(() => {
  if (!document.hidden && !$("#application").hidden && !$("#jobs-panel").hidden)
    loadJobs().catch(() => {});
}, 5000);

function labelCells(body) {
  const headers = [...body.closest("table").querySelectorAll("th")].map(
    (node) => node.textContent,
  );
  for (const row of body.rows)
    for (const [i, cell] of [...row.cells].entries())
      if (cell.colSpan === 1) cell.dataset.label = headers[i];
}
async function loadSettings() {
  const form = $("#settings-form");
  form.hidden = true;
  $("#locations-form").hidden = true;
  try {
    const data = await api("/admin/settings");
    for (const key of [
      "site_url",
      "site_api_url",
      "qb_url",
      "qb_username",
      "download_tag",
    ])
      form.elements[key].value = data[key];
    form.qb_verify_tls.checked = data.qb_verify_tls;
    for (const key of ["site_api_key", "qb_password"]) {
      form.elements[key].value = "";
      form.elements[key].placeholder = data[key + "_configured"]
        ? "已配置（留空保留）"
        : "尚未配置";
    }
    const locations = await api("/download-locations");
    $("#location-rows").replaceChildren();
    locations.items.forEach(addLocationRow);
    settingsSnapshot = JSON.stringify([...new FormData(form)]);
    locationsSnapshot = locationFingerprint();
    updateDirty();
  } finally {
    form.hidden = false;
    $("#locations-form").hidden = false;
  }
}
formAction("#settings-form", async (form) => {
  const body = Object.fromEntries(new FormData(form));
  body.qb_verify_tls = form.qb_verify_tls.checked;
  body.site_api_key = body.site_api_key || null;
  body.qb_password = body.qb_password || null;
  await api("/admin/settings", { method: "PUT", body: JSON.stringify(body) });
  if (form.site_api_key.value)
    form.site_api_key.placeholder = "已配置（留空保留）";
  if (form.qb_password.value)
    form.qb_password.placeholder = "已配置（留空保留）";
  form.site_api_key.value = "";
  form.qb_password.value = "";
  settingsSnapshot = JSON.stringify([...new FormData(form)]);
  updateDirty();
  settingsRevision++;
  for (const service of Object.keys(connectionTests))
    connectionTests[service] = { state: "stale" };
  renderServiceStatus();
  $("#connection-result").hidden = true;
  notice("连接设置已保存，请分别测试两个服务的连接");
  await loadSetupGuide();
});
for (const [selector, service] of [
  ["#test-mteam", "mteam"],
  ["#test-qb", "qbittorrent"],
])
  $(selector).addEventListener("click", () =>
    busy($(selector), async () => {
      if (settingsDirty()) {
        notice("请先保存连接设置，再测试新配置", true);
        return;
      }
      const box = $("#connection-result");
      const name = service === "mteam" ? "M-Team" : "qBittorrent";
      box.hidden = false;
      box.replaceChildren(element("p", `正在测试已保存的 ${name} 连接…`));
      const testRevision = settingsRevision;
      connectionTests[service] = { state: "testing" };
      renderServiceStatus();
      try {
        const result = await api("/admin/connections/" + service + "/test", {
          method: "POST",
        });
        if (testRevision !== settingsRevision) return;
        box.replaceChildren(element("h3", `${name} 连接成功`));
        if (result.version) box.append(element("p", `版本：${result.version}`));
        if (result.username)
          box.append(element("p", `账号：${result.username}`));
        if (result.default_save_path)
          box.append(
            element(
              "p",
              `默认保存目录：${result.default_save_path}（未验证写入权限）`,
            ),
          );
        connectionTests[service] = {
          state: "success",
          at: new Date().toLocaleTimeString(),
        };
        renderServiceStatus();
        notice(`${name} 连接测试成功`);
        box.append(
          element("p", `测试时间：${new Date().toLocaleTimeString()}`, "muted"),
          technicalDetails(result),
        );
      } catch (err) {
        if (testRevision !== settingsRevision) return;
        connectionTests[service] = {
          state: "failed",
          at: new Date().toLocaleTimeString(),
        };
        renderServiceStatus();
        box.replaceChildren(
          element("h3", `${name} 连接失败`),
          element("p", err.message, "error-text"),
          element(
            "p",
            "请核对已保存的地址、凭据与服务可达性，再重试。",
            "muted",
          ),
        );
        throw err;
      }
    }),
  );
function addLocationRow(
  value = {
    id: "loc_" + uniqueId().slice(0, 8),
    name: "",
    path: "/downloads",
    is_default: false,
  },
) {
  const row = element("div", undefined, "location-row");
  row.dataset.id = value.id;
  for (const [name, label] of [
    ["id", "ID"],
    ["name", "名称"],
    ["path", "容器内路径"],
  ]) {
    const wrap = element("label", label);
    const input = element("input");
    input.name = name;
    input.value = value[name];
    input.required = true;
    input.maxLength = name === "path" ? 1000 : name === "id" ? 64 : 100;
    wrap.append(input);
    if (name === "id") {
      const advanced = element("details", undefined, "location-advanced");
      advanced.append(element("summary", "高级：目录 ID"), wrap);
      row.append(advanced);
    } else row.append(wrap);
  }
  const label = element("label", "默认", "check");
  const radio = element("input");
  radio.type = "radio";
  radio.name = "default_location";
  radio.checked = value.is_default;
  label.prepend(radio);
  row.append(
    label,
    button("移除", () => row.remove()),
  );
  $("#location-rows").append(row);
}
$("#add-location").addEventListener("click", () => addLocationRow());
formAction("#locations-form", async () => {
  const items = [...document.querySelectorAll(".location-row")].map((row) => ({
    id: row.querySelector("[name=id]").value,
    name: row.querySelector("[name=name]").value,
    path: row.querySelector("[name=path]").value,
    is_default: row.querySelector("[type=radio]").checked,
  }));
  await api("/admin/download-locations", {
    method: "PUT",
    body: JSON.stringify({ items }),
  });
  locationsSnapshot = locationFingerprint();
  notice("目录预设已保存");
});
async function loadTokens() {
  const data = await api("/admin/tokens");
  $("#token-list").replaceChildren();
  for (const item of data.items) {
    const row = element("div", undefined, "token-item");
    const info = element("div", item.name);
    info.append(
      element(
        "p",
        item.scopes.map((scope) => scopeNames[scope] || scope).join(" · "),
        "muted",
      ),
      element(
        "p",
        `有效至 ${date(item.expires_at)}${item.revoked ? " · 已撤销" : ""}`,
        "muted",
      ),
    );
    row.append(info);
    if (!item.revoked)
      row.append(
        button("撤销", async () => {
          if (
            !confirm(
              `撤销“${item.name}”？使用该令牌的自动化将立即失去访问权限。`,
            )
          )
            return;
          await api("/admin/tokens/" + item.id, { method: "DELETE" });
          await loadTokens();
        }),
      );
    $("#token-list").append(row);
  }
}
formAction("#token-form", async (form) => {
  const data = await api("/admin/tokens", {
    method: "POST",
    body: JSON.stringify({
      name: form.elements.name.value,
      scopes: new FormData(form).getAll("scopes"),
      expires_in_days: Number(form.days.value),
    }),
  });
  tokenValue = data.token;
  $("#token-reveal").hidden = false;
  $("#copy-feedback").textContent = "";
  $("#new-token").textContent =
    "请立即复制并安全保存；刷新后不再显示：\n" + data.token;
  $("#new-token").hidden = false;
  await loadTokens();
});
$("#copy-token").addEventListener("click", () =>
  busy($("#copy-token"), async () => {
    try {
      await navigator.clipboard.writeText(tokenValue);
      $("#copy-feedback").textContent = " 已复制，请安全保存";
    } catch {
      $("#copy-feedback").textContent =
        " 浏览器不支持自动复制，请手动选择上方令牌复制";
    }
  }),
);
(async () => {
  try {
    const data = await api("/auth/me");
    if (data.kind === "session") {
      showApplication();
      await loadSetupGuide().catch((err) =>
        notice("配置状态暂不可用：" + err.message, true),
      );
    } else showLogin();
  } catch {
    showLogin();
  }
})();
