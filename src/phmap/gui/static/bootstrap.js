"use strict";

// Loaded before the application so missing/stale scripts cannot leave dead controls silently.
(() => {
  const showFailure = (detail) => {
    document.documentElement.dataset.phmapFailed = "true";
    document.querySelectorAll("dialog[open]").forEach((dialog) => {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    });
    const banner = document.getElementById("ui-error");
    const text = document.getElementById("ui-error-detail");
    if (banner) banner.hidden = false;
    if (text) text.textContent = detail;
    const form = document.getElementById("run-form");
    if (form) form.inert = true;
    ["submit", "validate", "new-run", "open-preferences"].forEach((id) => {
      const button = document.getElementById(id);
      if (button) button.disabled = true;
    });
  };
  window.addEventListener("error", (event) => {
    if (event.target instanceof HTMLScriptElement
      || (event.target instanceof HTMLLinkElement && event.target.rel === "stylesheet")) {
      showFailure("A required interface file could not load. Reload the interface; restart pHmap if this continues.");
    } else if (event instanceof ErrorEvent
      && (!document.documentElement.dataset.phmapReady || event.filename?.includes("/static/"))) {
      showFailure("The interface encountered an error. Reload the interface; restart pHmap if this continues.");
    }
  }, true);
  window.addEventListener("unhandledrejection", () => {
    showFailure("An interface action failed unexpectedly. Reload the interface; your saved calculations are unchanged.");
  });
  window.addEventListener("DOMContentLoaded", () => {
    const version = document.querySelector('meta[name="phmap-ui-version"]')?.content;
    if (!/^[0-9a-f]{16}$/.test(version || "")) {
      showFailure("This server is running outdated interface code. Quit and restart pHmap, then reload this page.");
    } else if (getComputedStyle(document.documentElement).getPropertyValue("--phmap-ui-ready").trim() !== "1") {
      showFailure("The interface stylesheet is missing or outdated. Reload this page to get the current styles.");
    } else if (document.documentElement.dataset.phmapReady !== "true") {
      showFailure("The interface scripts did not initialize. Reload to discard outdated files, or restart pHmap.");
    }
  });
})();
