// AT SUITE website: menu, monthly/yearly switch, copy buttons, order status. No inline scripts (CSP).
(() => {
  const menu = document.querySelector(".menu-toggle");
  const nav = document.getElementById("nav");
  if (menu && nav) {
    menu.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      menu.setAttribute("aria-expanded", String(open));
    });
  }

  // Pricing: one switch for every plan card. Remembered for the visit only.
  const scope = document.querySelector("[data-cycle]");
  document.querySelectorAll(".cycle button").forEach((b) => {
    b.addEventListener("click", () => {
      scope.dataset.cycle = b.dataset.value;
      document.querySelectorAll(".cycle button").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
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
        setTimeout(() => (b.textContent = t), 1500);
      } catch {
        const r = document.createRange();
        r.selectNodeContents(el);
        getSelection().removeAllRanges();
        getSelection().addRange(r);
      }
    });
  });

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
})();
