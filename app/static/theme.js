// Loaded before styles so saved preferences apply before the first paint.
(() => {
  "use strict";
  const storageKey = "mtd-theme";
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
  const normalize = (value) =>
    ["light", "dark"].includes(value) ? value : "system";
  let preference = "system";
  try {
    preference = normalize(localStorage.getItem(storageKey));
  } catch {
    // Private browsing and storage restrictions must not prevent startup.
  }
  function apply() {
    document.documentElement.dataset.theme =
      preference === "system"
        ? systemTheme.matches
          ? "dark"
          : "light"
        : preference;
    const select = document.querySelector("#theme-select");
    if (select) select.value = preference;
  }
  apply();
  systemTheme.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key === storageKey || event.key === null) {
      preference = normalize(event.newValue);
      apply();
    }
  });
  document.addEventListener("DOMContentLoaded", () => {
    const select = document.querySelector("#theme-select");
    if (!select) return;
    select.value = preference;
    select.addEventListener("change", () => {
      preference = normalize(select.value);
      try {
        localStorage.setItem(storageKey, preference);
      } catch {
        // Keep the choice for this page when persistent storage is unavailable.
      }
      apply();
    });
  });
})();
