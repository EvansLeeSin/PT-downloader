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
  if ($("#download-dialog").open) $("#download-dialog").close();
  $("#new-token").textContent = "";
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
});
$("#logout").addEventListener("click", () =>
  busy($("#logout"), async () => {
    await api("/auth/logout", { method: "POST" });
    $("#new-token").textContent = "";
    showLogin();
  }),
);
formAction("#search-form", async (form) => {
  searchState = {
    keyword: form.keyword.value.trim(),
    mode: form.mode.value,
    free_only: form.free_only.checked,
  };
  page = 1;
  await search();
});
async function search() {
  const params = new URLSearchParams({ ...searchState, page, page_size: 20 });
  const result = await api("/torrents/search?" + params);
  $("#results").replaceChildren();
  for (const item of result.items) {
    const row = element("tr");
    const title = element("td");
    const link = element("a", item.title);
    link.href = item.detail_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    title.append(link);
    if (item.description) title.append(element("p", item.description, "muted"));
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
      element("td", `${item.seeders} / ${item.leechers}`),
      promoCell,
      action,
    );
    $("#results").append(row);
  }
  if (!result.items.length) {
    const row = element("tr");
    const cell = element("td", "未找到资源", "muted");
    cell.colSpan = 5;
    row.append(cell);
    $("#results").append(row);
  }
  $("#page-label").textContent = `第 ${result.page} 页`;
  $("#prev-page").disabled = result.page <= 1;
  $("#next-page").disabled = !result.has_more;
}
for (const [selector, delta] of [
  ["#prev-page", -1],
  ["#next-page", 1],
])
  $(selector).addEventListener("click", async () => {
    const before = page;
    page += delta;
    try {
      await search();
    } catch (err) {
      page = before;
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
  const result = await api("/downloads", {
    method: "POST",
    headers: { "Idempotency-Key": selection.key },
    body: fingerprint,
  });
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
async function fetchJobs() {
  const data = await api("/downloads?limit=50&include_live=true");
  $("#jobs").replaceChildren();
  $("#jobs-count").textContent = `共 ${data.total} 条记录，显示最近 50 条。`;
  for (const job of data.items) {
    const row = element("tr");
    const first = element("td", job.download_status?.name || job.torrent_id);
    if (job.download_status?.name)
      first.append(element("p", job.torrent_id, "muted"));
    first.append(element("p", date(job.created_at), "muted"));
    const path = element("td", job.actual_save_path || job.save_path);
    if (job.actual_save_path && job.actual_save_path !== job.save_path)
      path.append(element("p", "请求：" + job.save_path, "muted"));
    const state = element("td");
    state.append(
      element(
        "span",
        statusNames[job.submission_status] || job.submission_status,
        "badge",
      ),
    );
    const action = element("td");
    const actions = element("div", undefined, "actions");
    actions.append(
      button("查看", async () => {
        const detail = await api("/downloads/" + job.id);
        $("#job-detail").textContent = JSON.stringify(detail, null, 2);
        $("#job-detail").hidden = false;
        await loadJobs();
      }),
    );
    const remove = button(
      "删除记录",
      async () => {
        const warning =
          job.submission_status === "uncertain"
            ? "该任务提交结果待确认，请先核对下载器。\n"
            : "";
        if (
          !confirm(
            warning +
              "仅删除本应用的提交记录，不会删除 qBittorrent 任务或文件。确定删除？",
          )
        )
          return;
        await api("/downloads/" + job.id, { method: "DELETE" });
        $("#job-detail").hidden = true;
        $("#job-detail").textContent = "";
        row.remove();
        notice("提交记录已删除，下载器任务和文件不受影响");
        // Discard any pre-deletion refresh before requesting the current list.
        if (jobsLoading) await jobsLoading.catch(() => {});
        await loadJobs();
      },
      "secondary danger",
    );
    remove.disabled = ["queued", "processing"].includes(job.submission_status);
    if (remove.disabled) remove.title = "排队或提交中的记录暂不能删除";
    actions.append(remove);
    action.append(actions);
    row.append(
      first,
      path,
      state,
      downloadProgress(job),
      element("td", job.message),
      action,
    );
    $("#jobs").append(row);
  }
}
$("#refresh-jobs").addEventListener("click", () =>
  busy($("#refresh-jobs"), loadJobs),
);
setInterval(() => {
  if (!$("#application").hidden && !$("#jobs-panel").hidden)
    loadJobs().catch(() => {});
}, 5000);
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
  form.site_api_key.value = "";
  form.qb_password.value = "";
  notice("连接设置已保存");
  await loadSettings();
});
for (const [selector, service] of [
  ["#test-mteam", "mteam"],
  ["#test-qb", "qbittorrent"],
])
  $(selector).addEventListener("click", () =>
    busy($(selector), async () => {
      const result = await api("/admin/connections/" + service + "/test", {
        method: "POST",
      });
      $("#connection-result").textContent = JSON.stringify(result, null, 2);
      $("#connection-result").hidden = false;
      notice("连接测试成功");
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
    row.append(wrap);
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
  notice("目录预设已保存");
});
async function loadTokens() {
  const data = await api("/admin/tokens");
  $("#token-list").replaceChildren();
  for (const item of data.items) {
    const row = element("div", undefined, "token-item");
    const info = element("div", item.name);
    info.append(
      element("p", item.scopes.join(" · "), "muted"),
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
  $("#new-token").textContent =
    "请立即复制并安全保存；刷新后不再显示：\n" + data.token;
  $("#new-token").hidden = false;
  await loadTokens();
});
(async () => {
  try {
    const data = await api("/auth/me");
    if (data.kind === "session") showApplication();
    else showLogin();
  } catch {
    showLogin();
  }
})();
