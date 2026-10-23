"use strict";

// At narrow window widths, the existing sidebar remains reachable as a drawer.
const sidebarMedia = window.matchMedia("(max-width: 780px)");

function setSidebarOpen(open, restoreFocus = false) {
  const visible = Boolean(open && sidebarMedia.matches);
  document.body.classList.toggle("sidebar-open", visible);
  $("sidebar-toggle").setAttribute("aria-expanded", String(visible));
  $("sidebar-backdrop").hidden = !visible;
  if (visible) $("ling-home").focus();
  else if (restoreFocus) $("sidebar-toggle").focus();
}

$("sidebar-toggle").addEventListener("click", () => {
  setSidebarOpen(!document.body.classList.contains("sidebar-open"));
});
$("sidebar-close").addEventListener("click", () => setSidebarOpen(false, true));
$("sidebar-backdrop").addEventListener("click", () => setSidebarOpen(false, true));
$("sidebar").addEventListener("click", (event) => {
  if (event.target.closest(".side-new, #tasks li, #sessions li, #settings")) setSidebarOpen(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) {
    setSidebarOpen(false, true);
    event.preventDefault();
  }
});
sidebarMedia.addEventListener("change", () => setSidebarOpen(false));
