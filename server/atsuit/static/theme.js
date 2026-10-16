// The site's look (set by an admin in Settings → Look): put on the page
// before it draws, from the last look this browser saw, so there's no flash of the wrong
// theme. common.js's AT.branding() then applies the server's current look.

// Passwords, tokens, keys and codes never sit in the address bar: a form is only
// ever sent by script (never by the browser itself, which would put what's typed
// in the address), and any such value that arrives in an address is wiped from it.
(() => {
  if (window.__atNoSecretsInUrl) return;
  window.__atNoSecretsInUrl = true;
  document.addEventListener("submit", (e) => e.preventDefault(), true);
  const SECRET = /^(password|new_password2?|pass|passwd|pw|pin|username|operator|token|node_token|access_token|ticket|secret|key|api_key|apikey|code|enrol_code|sync_code|setup_code|licence_key|admin_password)$/i;
  try {
    const q = new URLSearchParams(location.search);
    const hits = [...q.keys()].filter((k) => SECRET.test(k));
    if (hits.length) {
      hits.forEach((k) => q.delete(k));
      history.replaceState(history.state, "", location.pathname + (q.size ? "?" + q : "") + location.hash);
    }
  } catch (_) {}
})();

var ATTheme = (() => { // var: other scripts look for window.ATTheme
  const KEY = "atsuit_appearance";
  const ATTRS = ["theme", "density", "corners", "font", "motion"];
  function apply(a) {
    if (!a) return;
    const root = document.documentElement;
    ATTRS.forEach((k) => { if (a[k]) root.dataset[k] = a[k]; });
    root.style.colorScheme = a.theme === "light" ? "light" : a.theme === "auto" ? "light dark" : "dark";
  }
  function remember(a) { try { localStorage.setItem(KEY, JSON.stringify(a)); } catch (_) {} }
  try { apply(JSON.parse(localStorage.getItem(KEY) || "null")); } catch (_) {}
  return { apply, remember, ATTRS };
})();
