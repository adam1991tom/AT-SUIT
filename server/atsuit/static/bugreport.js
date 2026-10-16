// Report a bug: a form in a pop-up window, on the console's About tab and in the tech workspace's
// Help menu. The report goes to the admins' Bug reports page on this server; nothing is emailed.
// AT.reportBug() opens it.
(() => {
  const { esc, api, toast } = AT;
  let dlg = null;

  function build() {
    dlg = document.createElement("dialog");
    dlg.className = "bugdlg";
    dlg.setAttribute("aria-labelledby", "bugTitle");
    dlg.innerHTML = `<form method="dialog" id="bugForm">
      <h2 id="bugTitle">Report a bug</h2>
      <p class="small muted" style="margin-top:0">It goes to the admins on the Bug reports page in the console, with the version and system filled in. Nothing is emailed.</p>
      <label>What went wrong?</label><input name="title" maxlength="120" required autocomplete="off" placeholder="e.g. The stage timer froze in Hall A">
      <label>What were you doing, and what did you expect?</label><textarea name="description" rows="5" maxlength="4000" placeholder="Steps, the room, the time it happened"></textarea>
      <label>Screenshot (optional)</label><input name="screenshot" type="file" accept="image/png,image/jpeg,image/gif,image/webp">
      <p class="small muted" style="margin:.2rem 0 0">Or press Print Screen, then Ctrl+V here.</p>
      <div id="bugShot" class="hidden" style="margin-top:.4rem"><img alt="The screenshot to send" style="max-width:100%;max-height:30vh;border:1px solid var(--line);border-radius:var(--radius)">
        <div><button type="button" class="small danger" id="bugShotRm">Remove screenshot</button></div></div>
      <details style="margin-top:.6rem"><summary class="small">What else is sent</summary><div class="small muted" id="bugExtra"></div></details>
      <p class="small" id="bugErr" role="alert" style="color:var(--bad-ink);margin:.6rem 0 0"></p>
      <div class="row" style="margin-top:.6rem;justify-content:flex-end"><button type="button" id="bugCancel">Cancel</button><button class="primary" id="bugSend">Send report</button></div>
    </form>`;
    document.body.appendChild(dlg);
    const f = dlg.querySelector("form");
    let pasted = null;
    const shotBox = dlg.querySelector("#bugShot"), img = shotBox.querySelector("img");
    const showShot = (file) => {
      if (img.src) URL.revokeObjectURL(img.src);
      if (!file) { img.removeAttribute("src"); shotBox.classList.add("hidden"); return; }
      img.src = URL.createObjectURL(file); shotBox.classList.remove("hidden");
    };
    f.screenshot.onchange = () => { pasted = null; showShot(f.screenshot.files[0]); };
    dlg.querySelector("#bugShotRm").onclick = () => { pasted = null; f.screenshot.value = ""; showShot(null); };
    dlg.addEventListener("paste", (e) => {
      const file = [...(e.clipboardData?.files || [])].find((x) => x.type.startsWith("image/"));
      if (!file) return;
      e.preventDefault();
      pasted = file; f.screenshot.value = ""; showShot(file);
      say("");
    });
    const say = (m) => { dlg.querySelector("#bugErr").textContent = m; };
    dlg.querySelector("#bugCancel").onclick = () => dlg.close();
    let sending = false;
    f.onsubmit = async (e) => {
      e.preventDefault();
      if (sending) return;
      const title = f.title.value.trim();
      if (!title) { say("Say what went wrong first."); f.title.focus(); return; }
      const shot = pasted || f.screenshot.files[0];
      if (shot && shot.size > 8 * 1024 * 1024) { say("The screenshot is too big (8 MB at most)."); return; }
      const form = new FormData();
      form.append("title", title);
      form.append("description", f.description.value.trim());
      form.append("page", (location.pathname + location.hash).slice(0, 300));
      form.append("user_agent", navigator.userAgent.slice(0, 400));
      form.append("app_version", dlg.dataset.app || "");
      if (shot) form.append("screenshot", shot, shot.name || "screenshot.png");
      say(""); sending = true; const btn = dlg.querySelector("#bugSend"); btn.disabled = true; btn.textContent = "Sending…";
      try {
        await api("/api/bugs", { method: "POST", form });
        dlg.close(); f.reset(); pasted = null; showShot(null);
        toast("Thanks. Your report is with the admins.", "good");
      } catch (ex) { say(ex.message); }
      finally { sending = false; btn.disabled = false; btn.textContent = "Send report"; }
    };
  }

  AT.reportBug = async () => {
    if (!dlg) build();
    const app = window.atsuitApp;
    if (app && !dlg.dataset.app) dlg.dataset.app = String(await app.version().catch(() => "")).slice(0, 40);
    dlg.querySelector("#bugErr").textContent = "";
    dlg.querySelector("#bugExtra").innerHTML = `This page (<code>${esc(location.pathname + location.hash)}</code>), your browser${dlg.dataset.app ? `, the laptop app ${esc(dlg.dataset.app)}` : ""}, your name, and the AT SUITE version and system this server runs on. No passwords or keys.`;
    dlg.showModal();
    dlg.querySelector("[name=title]").focus();
  };
})();
