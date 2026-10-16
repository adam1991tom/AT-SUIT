// AT SUITE website: menu, monthly/yearly switch, copy buttons, order status. No inline scripts (CSP).
(() => {
  // One polite live region (in base.html) for short spoken updates: "Copied", "Showing monthly prices".
  const announce = (text) => {
    const el = document.getElementById("announce");
    if (!el) return;
    el.textContent = "";
    setTimeout(() => { el.textContent = text; }, 50);
  };

  const menu = document.querySelector(".menu-toggle");
  const nav = document.getElementById("nav");
  if (menu && nav) {
    menu.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      menu.setAttribute("aria-expanded", String(open));
    });
    // Escape closes the open menu and puts focus back on its button.
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && nav.classList.contains("open")) {
        nav.classList.remove("open");
        menu.setAttribute("aria-expanded", "false");
        menu.focus();
      }
    });
  }

  // Pricing: one switch for every plan card. Remembered for the visit only.
  const scope = document.querySelector("[data-cycle]");
  document.querySelectorAll(".cycle button").forEach((b) => {
    b.addEventListener("click", () => {
      scope.dataset.cycle = b.dataset.value;
      document.querySelectorAll(".cycle button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
      announce(b.dataset.value === "month" ? "Showing monthly prices" : "Showing yearly prices");
      document.querySelectorAll("a[data-buy]").forEach((a) => {
        const u = new URL(a.href, location.href);
        u.searchParams.set("cycle", b.dataset.value);
        a.href = u.pathname + u.search;
      });
    });
  });

  document.querySelectorAll("[data-copy]").forEach((b) => {
    b.addEventListener("click", async () => {
      const el = document.getElementById(b.dataset.copy);
      try {
        await navigator.clipboard.writeText(el.textContent.trim());
        const t = b.textContent;
        b.textContent = "Copied";
        announce("Copied");
        setTimeout(() => (b.textContent = t), 1500);
      } catch {
        const r = document.createRange();
        r.selectNodeContents(el);
        getSelection().removeAllRanges();
        getSelection().addRange(r);
      }
    });
  });

  // Sign-in link: the token is after "#" so it stays out of web addresses servers see. Move it into the
  // form and wipe it from the address bar and history.
  const linkToken = document.getElementById("link-token");
  if (linkToken) {
    const t = new URLSearchParams(location.hash.slice(1)).get("token") || "";
    linkToken.value = t;
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  }

  // Forms: stop a second click sending twice.
  document.querySelectorAll("form").forEach((f) => {
    f.addEventListener("submit", (e) => {
      if (f.dataset.sent) { e.preventDefault(); return; }
      if (f.checkValidity()) f.dataset.sent = "1";
    });
  });

  // Confirm before risky admin actions.
  document.querySelectorAll("[data-confirm]").forEach((el) => {
    el.addEventListener("click", (e) => { if (!confirm(el.dataset.confirm)) e.preventDefault(); });
  });

  // Thank-you page: wait for the payment to be confirmed and the key made.
  const wait = document.querySelector("[data-order]");
  if (wait) {
    let tries = 0;
    const poll = async () => {
      tries++;
      try {
        const r = await fetch(`/account/order/${encodeURIComponent(wait.dataset.order)}`, { credentials: "same-origin" });
        if (r.ok) {
          const d = await r.json();
          if (d.licence) { location.href = "/account#licence"; return; }
        }
      } catch {}
      if (tries < 40) setTimeout(poll, 3000);
      else wait.textContent = "This is taking longer than usual. Your key will be emailed to you; you can close this page.";
    };
    poll();
  }

  // Boxes that scroll sideways (wide tables, code, licence keys) need a focus stop so a keyboard can scroll them.
  const labels = new Set();
  const scrollers = () => document.querySelectorAll(".table-wrap, pre, .key").forEach((el) => {
    const scrolls = el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
    if (scrolls && !el.hasAttribute("tabindex")) {
      el.tabIndex = 0;
      el.dataset.scrolls = "1";
      if (!el.getAttribute("role")) el.setAttribute("role", "region");
      if (!el.getAttribute("aria-label")) {
        const h = el.closest("section, .card, main")?.querySelector("h1, h2, h3");
        const what = el.matches(".table-wrap") ? "Table" : el.matches(".key") ? "Licence key" : "Code";
        let label = h ? `${what}: ${h.textContent.trim()}` : what;
        for (let n = 2; labels.has(label); n++) label = `${label.replace(/ \(\d+\)$/, "")} (${n})`;
        labels.add(label);
        el.setAttribute("aria-label", label);
      }
    } else if (!scrolls && el.dataset.scrolls) {
      el.removeAttribute("tabindex");
      delete el.dataset.scrolls;
    }
  });
  scrollers();
  let resizing;
  addEventListener("resize", () => { clearTimeout(resizing); resizing = setTimeout(scrollers, 150); });
})();
