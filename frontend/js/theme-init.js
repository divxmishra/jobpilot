// Applies the persisted theme before first paint (no flash). Kept tiny and synchronous.
(function () {
  try {
    var t = localStorage.getItem("jobpilot.theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage unavailable: follow system theme */ }
})();
