// The site's look (set by an admin in Admin → General → Appearance): put on the page
// before it draws, from the last look this browser saw, so there's no flash of the wrong
// theme. common.js's AT.branding() then applies the server's current look.
var ATTheme = (() => { // var: other scripts look for window.ATTheme
  const KEY = "atsuit_appearance";
  const ATTRS = ["theme", "density", "corners", "font", "motion"];
  function apply(a) {
    if (!a) return;
    const root = document.documentElement;
    ATTRS.forEach((k) => { if (a[k]) root.dataset[k] = a[k]; });
    root.style.colorScheme = a.theme === "light" ? "light" : "dark";
  }
  function remember(a) { try { localStorage.setItem(KEY, JSON.stringify(a)); } catch (_) {} }
  try { apply(JSON.parse(localStorage.getItem(KEY) || "null")); } catch (_) {}
  return { apply, remember, ATTRS };
})();
