// End-to-end: the real app against a real AT-SUITE server.
const { _electron: electron, test, expect } = require("@playwright/test");
const { execFileSync } = require("child_process");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");

test.describe.configure({ mode: "serial" });

const env = JSON.parse(fs.readFileSync(path.join(os.tmpdir(), "atsuit-test.json"), "utf8"));
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "atsuit-node-"));
const preset = path.join(userData, "preset.json");
let app;

async function launch(extra = []) {
  const args = [path.join(__dirname, ".."), ...extra];
  if (process.platform === "linux") args.push("--no-sandbox");
  return electron.launch({
    args,
    env: { ...process.env, ATSUIT_NODE_USERDATA: userData, ATSUIT_NODE_PRESET: preset, ATSUIT_NODE_NO_UPDATES: "1" },
  });
}

async function mainPage(a) {
  for (let i = 0; i < 100; i++) {
    for (const w of a.windows()) if (w.url().includes("/node")) return w;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("main window never opened /node");
}

// A copy of this laptop that reaches the server through a link the test can cut and restore,
// like the venue network going down. Returns { start() to launch the app, cut(), restore() }.
async function laptopOnLink() {
  const target = new URL(env.base);
  const conns = new Set();
  const link = net.createServer((c) => {
    const up = net.connect(Number(target.port), target.hostname);
    conns.add(c); conns.add(up);
    c.pipe(up).pipe(c);
    const end = () => { c.destroy(); up.destroy(); conns.delete(c); conns.delete(up); };
    c.on("error", end); up.on("error", end); c.on("close", end); up.on("close", end);
  });
  await new Promise((r) => link.listen(0, "127.0.0.1", r));
  const port = link.address().port;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "atsuit-node-link-"));
  fs.cpSync(userData, dir, { recursive: true });
  const conf = JSON.parse(fs.readFileSync(path.join(dir, "node.json"), "utf8"));
  fs.writeFileSync(path.join(dir, "node.json"), JSON.stringify({ ...conf, server: `http://127.0.0.1:${port}`, overlay: null }));
  return {
    cut: () => new Promise((r) => { if (!link.listening) return r(); link.close(() => r()); for (const c of conns) c.destroy(); }),
    restore: () => new Promise((r) => link.listen(port, "127.0.0.1", r)),
    start: () => {
      const args = [path.join(__dirname, "..")];
      if (process.platform === "linux") args.push("--no-sandbox");
      return electron.launch({ args, env: { ...process.env, ATSUIT_NODE_USERDATA: dir, ATSUIT_NODE_PRESET: path.join(dir, "none.json"), ATSUIT_NODE_NO_UPDATES: "1" } });
    },
  };
}

async function popupWindows(a) {
  return a.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().filter((w) => w.webContents.getURL().includes("popup.html")).map((w) => ({
      id: w.id, onTop: w.isAlwaysOnTop(), focusable: w.isFocusable(), focused: w.isFocused(), visible: w.isVisible(),
      muted: w.webContents.isAudioMuted(), audible: w.webContents.isCurrentlyAudible(), url: decodeURIComponent(w.webContents.getURL()),
    })));
}

async function benSays(text, roomName = "HD", priority = "normal") {
  const login = await fetch(`${env.base}/api/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "ben", password: "ben-password-1" }),
  });
  const cookie = login.headers.get("set-cookie").split(";")[0];
  const chans = await (await fetch(`${env.base}/api/comms/channels`, { headers: { Cookie: cookie } })).json();
  const ch = chans.find((c) => c.kind === "room" && c.name === roomName);
  const r = await fetch(`${env.base}/api/comms/channels/${ch.id}/messages`, {
    method: "POST", headers: { "Content-Type": "application/json", Cookie: cookie }, body: JSON.stringify({ body: text, priority }),
  });
  expect(r.ok).toBeTruthy();
}

async function benHelp(text, roomName = "HD") {
  const login = await fetch(`${env.base}/api/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "ben", password: "ben-password-1" }),
  });
  const cookie = login.headers.get("set-cookie").split(";")[0];
  const boot = await (await fetch(`${env.base}/api/bootstrap`, { headers: { Cookie: cookie } })).json();
  const room = boot.rooms.find((r) => r.name === roomName);
  const r = await fetch(`${env.base}/api/comms/help`, {
    method: "POST", headers: { "Content-Type": "application/json", Cookie: cookie }, body: JSON.stringify({ room_id: room.id, category: "Video", description: text, priority: "urgent" }),
  });
  expect(r.ok).toBeTruthy();
}

test.afterEach(async () => {
  if (app) { await app.close().catch(() => {}); app = null; }
});

test("a new laptop enrols once from the IT preset, then restarts into the workspace", async () => {
  fs.writeFileSync(preset, JSON.stringify({ server: env.base, enrol_code: env.code, name: "testlap1" }));
  const a = await launch();
  const exited = new Promise((r) => a.process().once("exit", r));
  await exited; // enrolled, then relaunched itself (the relaunch is a separate process)
  const conf = JSON.parse(fs.readFileSync(path.join(userData, "node.json"), "utf8"));
  expect(conf.server).toBe(env.base);
  expect(conf.token_enc || conf.token_plain).toBeTruthy();
  expect(conf.popups).toBe(false); // nothing pops up until a tech says this is the backup PC
  // Kill the relaunched copy so the next test owns the single-instance lock.
  await new Promise((r) => setTimeout(r, 1500));
  try { execFileSync(process.platform === "win32" ? "taskkill" : "pkill", process.platform === "win32" ? ["/F", "/IM", "electron.exe"] : ["-f", `electron.*${path.join(__dirname, "..")}`]); } catch (_) {}
  await new Promise((r) => setTimeout(r, 1000));
  fs.rmSync(preset);
});

// Techs sign in with their own account. A password someone else set is changed first (newPassword).
async function start(page, { user, password, newPassword, room, mode }) {
  await expect(page.locator("#start")).toBeVisible();
  if (user !== undefined) await page.fill("#startForm [name=username]", user);
  if (password !== undefined) await page.fill("#startForm [name=password]", password);
  if (room) await page.click(`#startRooms [data-room] >> text=${room}`);
  if (mode) await page.check(`#startForm [name=mode][value=${mode}]`);
  await page.click("#startForm button.primary");
  if (newPassword) {
    await expect(page.locator("#startForm .pw-new")).toBeVisible();
    await page.fill("#startForm [name=new_password]", newPassword);
    await page.fill("#startForm [name=new_password2]", newPassword);
    await page.click("#startForm button.primary");
  }
  await expect(page.locator("#ws")).toBeVisible();
}

const conf = () => JSON.parse(fs.readFileSync(path.join(userData, "node.json"), "utf8"));

test("after enrolment the tech signs in with their account, picks the room and Main or Backup, and sets their own password", async () => {
  app = await launch();
  const page = await mainPage(app);
  expect(app.windows().some((w) => w.url().includes("setup.html"))).toBeFalsy();
  await expect(page.locator("#start h1")).toHaveText("Start on TESTLAP1");
  await expect(page.locator("#login")).toBeHidden();
  if (process.env.ATSUIT_SHOTS) await page.screenshot({ path: path.join(process.env.ATSUIT_SHOTS, "start.png") });
  await expect(page.locator("#startForm [name=operator]")).toBeHidden(); // no name-only sign-in on a new site
  await start(page, { user: "amy", password: "amy-password-1", newPassword: "amy-own-pass-1", room: "HD", mode: "main" });
  await expect(page.locator("#room option:checked")).toHaveText("HD");
  await expect(page.locator("#who")).toHaveText("Amy on TESTLAP1");
  await expect(page.locator("#modePill")).toHaveText("Main PC");
  expect(conf().popups).toBe(false);
  // The app's version is in the top bar; This laptop has the updates (off here: not an installed app).
  const version = require("../package.json").version;
  await expect(page.locator("#topVer")).toHaveText(`v${version}`);
  await page.click("#topVer");
  await expect(page.locator("#appVer")).toHaveText(`AT-SUITE Node ${version}`);
  await expect(page.locator("#updText")).toContainText("Updates come from the server");
  await expect(page.locator("#updBtn")).toBeDisabled();
  await expect(page.locator("#updLaunch")).toBeChecked();
  await page.uncheck("#updLaunch");
  await expect.poll(() => conf().update_on_launch).toBe(false);
  await page.check("#updLaunch");
  await expect.poll(() => conf().update_on_launch).toBe(true);
  await page.click("#laptopBtn"); // close it again
});

test("the main PC: no pop-ups and no notifications of any kind", async () => {
  app = await launch();
  const page = await mainPage(app);
  await expect(page.locator("#ws")).toBeVisible(); // still signed in after a restart
  await expect(page.locator("#conn")).toHaveText("live");
  await benSays("Mic 3 is on stage");
  await benSays("Fire alarm test at 3pm", "RH", "urgent");
  await benHelp("Projector off in HD");
  await expect(page.locator("#chat")).toContainText("Mic 3 is on stage");
  await expect(page.locator("#helpList")).toContainText("Projector off in HD");
  await page.waitForTimeout(1500);
  expect(await popupWindows(app)).toEqual([]);
  await expect(page.locator(".toast")).toHaveCount(0); // not even inside the window
});

test("a workspace window pops out into a window of its own, stays on top if asked, and docks back", async () => {
  app = await launch();
  const page = await mainPage(app);
  await expect(page.locator(".dk-win:has(#chat)")).toBeVisible(); // the desk lays the windows out once the page has loaded
  const gid = await page.evaluate(() => ATDesk.tree.groupOf(ATDesk.layout(), "chat").id);
  const [pop] = await Promise.all([app.waitForEvent("window"), page.click(`[data-gid="${gid}"] [aria-label="Pop out"]`)]);
  await expect(pop.locator(".dk-popmain #chat")).toBeVisible();
  await benSays("Hello from the pop-out test");
  await expect(pop.locator("#chat")).toContainText("Hello from the pop-out test"); // still live in its own window
  const popped = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()
    .filter((w) => !w.isDestroyed() && w.webContents.getURL() === "about:blank").map((w) => ({ onTop: w.isAlwaysOnTop(), muted: w.webContents.isAudioMuted() })));
  expect(await popped()).toEqual([{ onTop: false, muted: true }]);
  await pop.check("[data-top]");
  await expect.poll(popped).toEqual([{ onTop: true, muted: true }]);
  await pop.click("[data-back]");
  await expect(page.locator(".dk-pane #chat")).toBeVisible();
  await expect.poll(popped).toEqual([]);
});

test("the laptop reports its health, and shows its screens only while a manager looks", async () => {
  const login = await fetch(`${env.base}/api/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "correct-horse" }),
  });
  const Cookie = login.headers.get("set-cookie").split(";")[0];
  const as = (method, url, body) => fetch(`${env.base}${url}`, { method, headers: { "Content-Type": "application/json", Cookie }, body: body && JSON.stringify(body) }).then((r) => r.json());
  await as("PUT", "/api/admin/settings", { laptop_snapshots: true });
  try {
    app = await launch();
    const page = await mainPage(app);
    await expect(page.locator("#ws")).toBeVisible();
    const me = async () => (await as("GET", "/api/fleet/nodes")).find((n) => n.kind === "tech" && n.info?.system);
    await expect.poll(me, { timeout: 15000 }).toBeTruthy();
    const sys = (await me()).info.system;
    expect(sys.mem_total).toBeGreaterThan(0);
    expect(sys.cores).toBeGreaterThan(0);
    expect(sys.displays.length).toBeGreaterThan(0);
    await expect(page.locator("#watched")).toBeHidden(); // nobody looking yet

    // A manager opens the laptop on the console: within a heartbeat it sends its screens, and says so.
    const id = (await me()).id;
    const health = () => as("GET", `/api/fleet/nodes/${id}/health`);
    await expect.poll(async () => (await health()).snapshot?.shots?.length || 0, { timeout: 25000, intervals: [2000] }).toBeGreaterThan(0);
    const shot = (await health()).snapshot.shots[0];
    expect(shot.image).toMatch(/^data:image\/jpeg;base64,/);
    await expect(page.locator("#watched")).toBeVisible();
  } finally {
    await as("PUT", "/api/admin/settings", { laptop_snapshots: false });
  }
});

test("the timer keeps working on the laptop while the server is out of reach, and every screen agrees after", async () => {
  const login = await fetch(`${env.base}/api/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "correct-horse" }),
  });
  const Cookie = login.headers.get("set-cookie").split(";")[0];
  const as = (method, url, body) => fetch(`${env.base}${url}`, { method, headers: { "Content-Type": "application/json", Cookie }, body: body && JSON.stringify(body) }).then((r) => r.json());
  const hd = (await as("GET", "/api/bootstrap")).rooms.find((r) => r.name === "HD").id;
  await as("POST", `/api/timers/${hd}/stop`);
  const { cut, restore, start } = await laptopOnLink();
  app = await start();
  const page = await mainPage(app);
  await expect(page.locator("#ws")).toBeVisible();
  const clock = page.locator("[data-clock]").first();
  await expect(clock).toHaveText("--:--");

  // The server can't be reached: the presses still work here.
  await cut();
  await page.locator('[data-min="10"]').first().click();
  await expect(page.locator("#timerOffline")).toBeVisible();
  await expect(page.locator("#timerOffline")).toContainText("1 change");
  await expect(clock).toHaveText(/^(10:00|09:5\d)$/);
  await page.locator('[data-add="60000"]').first().click();
  await expect(clock).toHaveText(/^(11:00|10:5\d)$/);
  await expect(page.locator("#timerOffline")).toContainText("2 changes");
  expect((await as("GET", `/api/timers/${hd}`)).playback).toBe("stop"); // nothing reached the server

  // It's back: the presses go to the server as of when they were made, and the screen stays as it was.
  await restore();
  await expect(page.locator("#timerOffline")).toBeHidden({ timeout: 10000 });
  const s = await as("GET", `/api/timers/${hd}`);
  expect(s.running).toBe(true);
  expect(s.duration_ms).toBe(600000);
  expect(s.added_ms).toBe(60000);
  expect(s.remaining_ms).toBeGreaterThan(600000);
  await expect(clock).toHaveText(/^(11:00|10:[45]\d)$/);
  await app.close(); app = null;
  await cut();
  await as("POST", `/api/timers/${hd}/stop`);
});

test("the workspace opens from the laptop's copy when the server is down, and runs the timer", async () => {
  const login = await fetch(`${env.base}/api/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "correct-horse" }),
  });
  const Cookie = login.headers.get("set-cookie").split(";")[0];
  const as = (method, url, body) => fetch(`${env.base}${url}`, { method, headers: { "Content-Type": "application/json", Cookie }, body: body && JSON.stringify(body) }).then((r) => r.json());
  const hd = (await as("GET", "/api/bootstrap")).rooms.find((r) => r.name === "HD").id;
  await as("POST", `/api/timers/${hd}/preset/10`);

  // This laptop reaches the server through a link we can cut (the venue network).
  const { cut, restore, start } = await laptopOnLink();

  // A normal day: the workspace opens from the server, and the laptop keeps a copy.
  app = await start();
  let page = await mainPage(app);
  await expect(page.locator("#ws")).toBeVisible();
  await expect(page.locator("[data-clock]").first()).toHaveText(/^(10:00|09:\d\d)$/);
  await page.waitForTimeout(1500);
  await app.close();

  // The server is down when the app starts: the workspace still opens, with the timer where it should be.
  await cut();
  app = await start();
  page = await mainPage(app);
  await expect(page.locator("#ws")).toBeVisible({ timeout: 20000 });
  const clock = page.locator("[data-clock]").first();
  await expect(clock).toHaveText(/^09:\d\d$/);
  await page.locator('[data-add="60000"]').first().click();
  await expect(page.locator("#timerOffline")).toBeVisible();
  await expect(clock).toHaveText(/^10:\d\d$/);
  expect((await as("GET", `/api/timers/${hd}`)).added_ms).toBe(0);

  // The server is back: the press reaches it.
  await restore();
  await expect(page.locator("#timerOffline")).toBeHidden({ timeout: 15000 });
  expect((await as("GET", `/api/timers/${hd}`)).added_ms).toBe(60000);
  await app.close(); app = null;
  await cut();
  await as("POST", `/api/timers/${hd}/stop`);
});

test("captions are made on the laptop, and carry on through a network drop", async () => {
  const models = process.env.ATSUIT_TEST_MODELS;
  test.skip(!models, "set ATSUIT_TEST_MODELS to a folder holding the speech model");
  test.setTimeout(240000);
  const sample = models && fs.readdirSync(models).map((d) => path.join(models, d, "test_wavs", "0.wav")).find((f) => fs.existsSync(f));
  test.skip(!sample, "the model folder has no test_wavs/0.wav");
  // The test recording and two seconds of quiet, so each time round ends a caption line.
  const pcm = Buffer.concat([fs.readFileSync(sample).subarray(44), Buffer.alloc(16000 * 2 * 2)]);
  const head = Buffer.from(fs.readFileSync(sample).subarray(0, 44));
  head.writeUInt32LE(36 + pcm.length, 4); head.writeUInt32LE(pcm.length, 40);
  const wav = path.join(userData, "mic.wav");
  fs.writeFileSync(wav, Buffer.concat([head, pcm]));
  const login = await fetch(`${env.base}/api/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "correct-horse" }),
  });
  const Cookie = login.headers.get("set-cookie").split(";")[0];
  const as = (method, url, body) => fetch(`${env.base}${url}`, { method, headers: { "Content-Type": "application/json", Cookie }, body: body && JSON.stringify(body) }).then((r) => r.json());
  const hd = (await as("GET", "/api/bootstrap")).rooms.find((r) => r.name === "HD").id;
  await as("PUT", `/api/captions/${hd}/settings`, { vocabulary: "Yellow Lambs" });
  // The test recording, over and over, as the laptop's microphone.
  app = await launch(["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${wav}`]);
  const page = await mainPage(app);
  await expect(page.locator("#ws")).toBeVisible();
  // The app fetches the model from the server in the background.
  await expect.poll(() => page.evaluate(() => window.atsuitApp.captions.state().then((s) => s.state)), { timeout: 120000, intervals: [1000] }).toBe("ready");

  // The test holds the line to the server, so it can cut it.
  let socket = null, cut = false;
  await page.routeWebSocket(/\/ws\/audio\//, (ws) => {
    if (cut) return ws.close();
    socket = ws;
    ws.connectToServer();
  });
  await page.reload(); // the route only reaches pages loaded after it
  await expect(page.locator("#ws")).toBeVisible();
  await expect(page.locator("#mic option").first()).toBeAttached(); // the mic list is ready
  await page.evaluate(() => document.getElementById("micBtn").click());
  await expect(page.locator("#micNote")).toContainText("on this laptop", { timeout: 15000 });
  const since = async (t) => ((await as("GET", `/api/captions/${hd}/recent`)).finals_at || []).filter((x) => x > t).length;
  const heard = async () => ((await as("GET", `/api/captions/${hd}/recent`)).finals || []).join(" ").toUpperCase();
  await expect.poll(heard, { timeout: 60000, intervals: [1000] }).toContain("YELLOW LAMBS"); // the venue's vocabulary, on the laptop

  // The network drops for 20 seconds: the laptop keeps listening, and catches up when it's back.
  cut = true;
  socket.close();
  const t0 = (await as("GET", `/api/captions/${hd}/recent`)).now;
  await expect(page.locator("#micNote")).toContainText("No connection to the server");
  await new Promise((r) => setTimeout(r, 20000));
  expect(await since(t0)).toBe(0); // nothing reaches the server meanwhile
  cut = false;
  await expect(page.locator("#micNote")).toContainText("Back online", { timeout: 20000 });
  expect(await since(t0)).toBeGreaterThan(1); // what was said during the drop, all at once
  await page.evaluate(() => document.getElementById("micBtn").click());
});

test("caption text can stay on the laptop, which feeds the room's screens and keeps the transcript", async () => {
  test.setTimeout(240000);
  const models = process.env.ATSUIT_TEST_MODELS;
  const sample = models && fs.readdirSync(models).map((d) => path.join(models, d, "test_wavs", "0.wav")).find((f) => fs.existsSync(f));
  const login = await fetch(`${env.base}/api/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "correct-horse" }),
  });
  const Cookie = login.headers.get("set-cookie").split(";")[0];
  const as = (method, url, body) => fetch(`${env.base}${url}`, { method, headers: { "Content-Type": "application/json", Cookie }, body: body && JSON.stringify(body) }).then((r) => r.json());
  const hd = (await as("GET", "/api/bootstrap")).rooms.find((r) => r.name === "HD").id;
  await as("PUT", "/api/admin/settings", { captions_private: true });
  await as("POST", `/api/timers/${hd}/stop`);
  try {
    const extra = sample ? ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${path.join(userData, "mic.wav")}`] : [];
    app = await launch(extra);
    const page = await mainPage(app);
    await expect(page.locator("#ws")).toBeVisible();
    await expect(page.locator("#capSave")).not.toHaveClass(/hidden/); // in the Captions window

    // The tech turns on this laptop's screen feed, and a screen on the venue network connects to it.
    const feedSwitch = (on) => page.evaluate((v) => { const b = document.getElementById("feedOn"); b.checked = v; b.dispatchEvent(new Event("change")); }, on);
    await feedSwitch(true);
    await expect(page.locator("#feedNote")).toContainText(`/captions/${hd}`);
    const { port } = await page.evaluate(() => window.atsuitApp.screens());
    const feed = `http://127.0.0.1:${port}`;
    const capPage = await fetch(`${feed}/captions/${hd}`);
    expect(capPage.status).toBe(200);
    expect(await capPage.text()).toContain("<title>Captions</title>");
    expect((await fetch(`${feed}/timer/${hd}`)).status).toBe(200);
    expect((await fetch(`${feed}/api/auth/me`)).status).toBe(404); // nothing but the screens' public readings
    expect((await (await fetch(`${feed}/api/captions/${hd}/recent`)).json()).finals).toEqual([]);
    const events = [];
    const screen = new WebSocket(`ws://127.0.0.1:${port}/ws?topics=timer:${hd},captions:${hd}`);
    screen.onmessage = (e) => events.push(JSON.parse(e.data));
    await expect.poll(() => events.some((e) => e.type === "hello")).toBe(true);

    // The timer on that screen follows the room's timer.
    await as("POST", `/api/timers/${hd}/preset/5`);
    await expect.poll(() => events.filter((e) => e.type === "timer" && e.data.duration_ms === 300000).length, { timeout: 10000 }).toBeGreaterThan(0);

    if (sample) {
      // The mic is captioned on this laptop: the screen gets the captions, the server never sees them.
      await expect.poll(() => page.evaluate(() => window.atsuitApp.captions.state().then((s) => s.state)), { timeout: 120000, intervals: [1000] }).toBe("ready");
      await page.reload();
      await expect(page.locator("#mic option").first()).toBeAttached();
      const t0 = (await as("GET", `/api/captions/${hd}/recent`)).now;
      await page.evaluate(() => document.getElementById("micBtn").click());
      await expect(page.locator("#micNote")).toContainText("The text stays here", { timeout: 15000 });
      await expect.poll(() => events.filter((e) => e.type === "final").map((e) => e.data.text).join(" ").toUpperCase(), { timeout: 60000, intervals: [1000] }).toContain("YELLOW LAMBS");
      await expect(page.locator("#capText")).toContainText(/yellow lambs/i);
      const recent = await as("GET", `/api/captions/${hd}/recent`);
      expect(recent.live).toBe(true);
      expect((recent.finals_at || []).filter((x) => x > t0)).toEqual([]); // nothing new reached the server
      expect(recent.partial).toBe("");
      expect((await (await fetch(`${feed}/api/captions/${hd}/recent`)).json()).finals.join(" ").toUpperCase()).toContain("YELLOW LAMBS");
      const t = await page.evaluate(() => window.atsuitApp.bubble.transcript());
      expect(t.lines).toBeGreaterThan(0);
      const files = fs.readdirSync(path.join(userData, "transcripts"));
      expect(fs.readFileSync(path.join(userData, "transcripts", files[files.length - 1]), "utf8").toUpperCase()).toContain("YELLOW LAMBS");
      await page.evaluate(() => document.getElementById("micBtn").click());
    }
    screen.close();
    await feedSwitch(false);
    await expect.poll(() => fetch(`${feed}/timer/${hd}`).then(() => "up", () => "down")).toBe("down");
  } finally {
    await as("PUT", "/api/admin/settings", { captions_private: false });
    await as("POST", `/api/timers/${hd}/stop`);
  }
});

test("the backup PC: pop-ups are silent, on top and never take focus", async () => {
  app = await launch();
  const page = await mainPage(app);
  await expect(page.locator("#conn")).toHaveText("live");
  await app.evaluate(({ Notification }) => {
    globalThis.toasts = 0;
    Notification.prototype.show = function () { globalThis.toasts++; };
  });
  // The tech makes this the backup PC in This laptop.
  await page.click("#laptopBtn");
  await page.check("[name=lapmode][value=backup]");
  await expect(page.locator("#modePill")).toHaveText("Backup PC");
  await expect(page.locator("#conn")).toHaveText("live");
  expect(conf().popups).toBe(true);
  // Something else (the slides) has the focus.
  await app.evaluate(({ BrowserWindow }) => {
    const w = new BrowserWindow({ width: 900, height: 600, title: "Slides" });
    w.loadURL("data:text/html,<h1>Slide 1</h1>");
    w.focus();
    globalThis.slides = w.id;
  });
  await page.waitForTimeout(500);
  const before = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getFocusedWindow()?.id ?? null);
  expect(before).toBe(await app.evaluate(() => globalThis.slides));

  await benSays("Speaker for HD is in the lobby");
  await expect.poll(async () => (await popupWindows(app)).length, { timeout: 10000 }).toBe(1);
  await expect(page.locator("#bellCount")).toHaveText("1"); // and in the window's notifications
  await new Promise((r) => setTimeout(r, 800));
  const [p] = await popupWindows(app);
  expect(p.url).toContain("Speaker for HD is in the lobby");
  expect(p.visible).toBe(true);
  expect(p.onTop).toBe(true);
  expect(p.focusable).toBe(false);
  expect(p.focused).toBe(false);
  const after = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getFocusedWindow()?.id ?? null);
  expect(after).toBe(before); // the pop-up did not take focus from the slides
  if (process.env.ATSUIT_SHOTS) {
    const shot = await app.evaluate(async ({ BrowserWindow }) =>
      (await BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().includes("popup.html")).webContents.capturePage()).toPNG().toString("base64"));
    fs.writeFileSync(path.join(process.env.ATSUIT_SHOTS, "popup.png"), Buffer.from(shot, "base64"));
    await page.screenshot({ path: path.join(process.env.ATSUIT_SHOTS, "workspace.png") });
  }

  // Silence: no window can play audio, none is playing, and no Windows toast was raised.
  const sound = await app.evaluate(({ webContents }) => ({
    all: webContents.getAllWebContents().length,
    muted: webContents.getAllWebContents().filter((w) => w.isAudioMuted()).length,
    audible: webContents.getAllWebContents().filter((w) => w.isCurrentlyAudible()).length,
    toasts: globalThis.toasts,
  }));
  expect(sound.audible).toBe(0);
  expect(sound.toasts).toBe(0);
  // Every app window is muted (the test's own "slides" window is the one exception).
  expect(sound.muted).toBe(sound.all - 1);

  // Web notifications are refused, and native dialogs never open.
  expect(await page.evaluate(() => Notification.requestPermission())).toBe("denied");
  expect(await page.evaluate(() => [window.confirm("x"), window.prompt("x")])).toEqual([false, null]);

  // Urgent messages from anywhere pop up too; clicking a pop-up dismisses it.
  await benSays("Fire alarm test at 3pm", "RH", "urgent");
  await expect.poll(async () => (await popupWindows(app)).length, { timeout: 10000 }).toBe(2);
  await app.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows().filter((w) => w.webContents.getURL().includes("popup.html")).forEach((w) => w.webContents.executeJavaScript("document.getElementById('x').click()"));
  });
  await expect.poll(async () => (await popupWindows(app)).length, { timeout: 5000 }).toBe(0);
});

test("the next day the tech starts again, without enrolling again", async () => {
  app = await launch();
  const page = await mainPage(app);
  await expect(page.locator("#ws")).toBeVisible();
  // Pretend the day was started yesterday.
  const db = path.join(env.data, "atsuit.db");
  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  execFileSync(python, ["-c", "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(\"UPDATE nodes SET room_day='2000-01-01'\"); c.commit()", db]);
  // The next heartbeat notices and shows the start screen, remembering Main/Backup; the next tech signs in.
  await expect(page.locator("#start")).toBeVisible({ timeout: 20000 });
  expect(app.windows().some((w) => w.url().includes("setup.html"))).toBeFalsy();
  await expect(page.locator("#startForm [name=username]")).toBeVisible();
  await expect(page.locator("#startForm [name=mode][value=backup]")).toBeChecked();
  await start(page, { user: "ben", password: "ben-password-1", room: "CC" });
  await expect(page.locator("#room option:checked")).toHaveText("CC");
  await expect(page.locator("#who")).toHaveText("Ben on TESTLAP1");
  // The room is the tech's call: Ben moves the laptop to RH from the top bar, then back.
  for (const room of ["RH", "CC"]) {
    await page.selectOption("#room", { label: room });
    await expect(page.locator("#roomAskText")).toHaveText(`Move this laptop to ${room}?`);
    const reloaded = page.waitForEvent("load"); // the workspace starts over in the new room
    await page.click("#roomMove");
    await reloaded;
    await expect(page.locator("#ws")).toBeVisible();
    await expect(page.locator("#room option:checked")).toHaveText(room);
  }
  // Sign out at the end of the day: back to the start screen, and nothing pops up.
  await page.click("#out");
  await expect(page.locator("#start")).toBeVisible();
  expect(conf().popups).toBe(false);
  // The next tech sees every room, with today's already picked.
  await expect(page.locator("#startRooms [data-room]")).toHaveCount(3);
  await expect(page.locator("#startRooms .tile.on")).toHaveText("CC");
});

// ------------------------------------------------------------- overlay --
async function adminCookie() {
  const login = await fetch(`${env.base}/api/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "correct-horse" }),
  });
  return login.headers.get("set-cookie").split(";")[0];
}

async function roomId(name) {
  const boot = await (await fetch(`${env.base}/api/bootstrap`, { headers: { Cookie: await adminCookie() } })).json();
  return boot.rooms.find((r) => r.name === name).id;
}

async function overlayWindows(a) {
  return a.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().filter((w) => w.getTitle() === "AT-SUITE Overlay").map((w) => ({
      id: w.id, onTop: w.isAlwaysOnTop(), focusable: w.isFocusable(), focused: w.isFocused(), visible: w.isVisible(),
      clickThrough: (globalThis.ignoreMouse || {})[w.id] === true,
      muted: w.webContents.isAudioMuted(), url: w.webContents.getURL(), bounds: w.getBounds(),
    })));
}

test("the overlay floats over the slides, lets clicks through and never takes focus", async () => {
  app = await launch();
  const page = await mainPage(app);
  // The laptop is still in CC today (it keeps its room after signing out), and the overlay follows its room.
  const rid = await roomId("CC");
  // Watch which windows let the mouse through.
  await app.evaluate(({ BrowserWindow }) => {
    globalThis.ignoreMouse = {};
    const orig = BrowserWindow.prototype.setIgnoreMouseEvents;
    BrowserWindow.prototype.setIgnoreMouseEvents = function (v, ...rest) { globalThis.ignoreMouse[this.id] = v; return orig.call(this, v, ...rest); };
  });
  // The slides have the focus.
  await app.evaluate(({ BrowserWindow }) => {
    const w = new BrowserWindow({ width: 900, height: 600, title: "Slides" });
    w.loadURL("data:text/html,<h1>Slide 1</h1>");
    w.focus();
    globalThis.slides = w.id;
  });
  await page.waitForTimeout(500);
  const before = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getFocusedWindow()?.id ?? null);
  expect(before).toBe(await app.evaluate(() => globalThis.slides));

  const r = await page.evaluate((id) => window.atsuitApp.overlay({ on: true, room_id: id, position: "bottom-right", size: "small", opacity: 0.8 }), rid);
  expect(r.ok).toBe(true);
  expect(r.target).toBe(`${env.base}/timer/${rid}?view=overlay`);
  await expect.poll(async () => (await overlayWindows(app)).filter((w) => w.visible).length, { timeout: 10000 }).toBe(1);
  await new Promise((res) => setTimeout(res, 500));
  const [o] = await overlayWindows(app);
  expect(o.url).toBe(`${env.base}/timer/${rid}?view=overlay`);
  expect(o.onTop).toBe(true);
  expect(o.focusable).toBe(false);
  expect(o.focused).toBe(false);
  expect(o.clickThrough).toBe(true);
  expect(o.muted).toBe(true);
  expect(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getFocusedWindow()?.id ?? null)).toBe(before);
  // No permissions at all for whatever page the overlay shows.
  const perm = await app.evaluate(async ({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows().find((x) => x.getTitle() === "AT-SUITE Overlay");
    return w.webContents.executeJavaScript("Notification.requestPermission()");
  });
  expect(perm).toBe("denied");

  // Bottom right of the main display, 16px in.
  const wa = await app.evaluate(({ screen }) => screen.getPrimaryDisplay().workArea);
  expect(o.bounds.x + o.bounds.width).toBe(wa.x + wa.width - 16);
  expect(o.bounds.y + o.bounds.height).toBe(wa.y + wa.height - 16);
  expect(o.bounds.width).toBe(320);

  // Moves and resizes in place; the same window, still not focused.
  let s = await page.evaluate(() => window.atsuitApp.overlay({ position: "top-left", size: "large" }));
  expect(s.bounds).toEqual({ x: wa.x + 16, y: wa.y + 16, width: 600, height: 200 });
  s = await page.evaluate(() => window.atsuitApp.overlay({ position: "bottom-bar", display: 5 })); // no 6th display: the main one
  expect(s.bounds).toEqual({ x: wa.x, y: wa.y + wa.height - 110, width: wa.width, height: 110 });
  expect(s.displays.length).toBeGreaterThanOrEqual(1);
  // Top centre (the default), and it stays there once the window has shown and loaded.
  s = await page.evaluate(() => window.atsuitApp.overlay({ position: "top-center", size: "medium", display: 0 }));
  expect(s.bounds).toEqual({ x: wa.x + Math.round((wa.width - 440) / 2), y: wa.y + 16, width: 440, height: 150 });
  await new Promise((res) => setTimeout(res, 1000));
  expect((await overlayWindows(app))[0].bounds).toEqual(s.bounds);
  expect((await overlayWindows(app)).map((w) => w.id)).toEqual([o.id]);
  expect(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getFocusedWindow()?.id ?? null)).toBe(before);

  // Only http(s) pages.
  const bad = await page.evaluate(() => window.atsuitApp.overlay({ url: "file:///etc/passwd" }));
  expect(bad.ok).toBe(false);
  expect(bad.target).toBe(`${env.base}/timer/${rid}?view=overlay`);
  // A tech's own page.
  s = await page.evaluate((u) => window.atsuitApp.overlay({ url: u }), `${env.base}/api/health`);
  expect(s.target).toBe(`${env.base}/api/health`);
  await expect.poll(async () => (await overlayWindows(app))[0]?.url).toBe(`${env.base}/api/health`);
  // The window is see-through: where the page draws nothing, the slides show.
  await new Promise((res) => setTimeout(res, 500));
  const alpha = await app.evaluate(async ({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows().find((x) => x.getTitle() === "AT-SUITE Overlay");
    const img = await w.webContents.capturePage();
    const { width, height } = img.getSize();
    const px = img.toBitmap();
    return px[((height - 2) * width + (width - 2)) * 4 + 3];
  });
  expect(alpha).toBe(0);
  s = await page.evaluate(() => window.atsuitApp.overlay({ url: "" })); // back to the room timer
  expect(s.target).toBe(`${env.base}/timer/${rid}?view=overlay`);
  expect(conf().overlay).toMatchObject({ on: true, url: "", position: "top-center", size: "medium", room_id: rid });

  // Off.
  s = await page.evaluate(() => window.atsuitApp.overlay({ on: false }));
  expect(s.on).toBe(false);
  expect(await overlayWindows(app)).toEqual([]);
  expect(conf().overlay.on).toBe(false);
  expect((await page.evaluate(() => window.atsuitApp.overlayState())).on).toBe(false);
});

test("an overlay left on comes back after a restart, and the server knows", async () => {
  app = await launch();
  let page = await mainPage(app);
  const rid = await roomId("CC"); // the laptop's room today
  const s = await page.evaluate((id) => window.atsuitApp.overlay({ on: true, room_id: id, position: "top-right", size: "medium" }), rid);
  expect(s.ok).toBe(true);
  await expect.poll(async () => (await overlayWindows(app)).filter((w) => w.visible).length, { timeout: 10000 }).toBe(1);
  await app.close();
  app = null;

  app = await launch();
  page = await mainPage(app);
  await expect.poll(async () => (await overlayWindows(app)).filter((w) => w.visible).length, { timeout: 15000 }).toBe(1);
  const [o] = await overlayWindows(app);
  const wa = await app.evaluate(({ screen }) => screen.getPrimaryDisplay().workArea);
  expect(o.url).toBe(`${env.base}/timer/${rid}?view=overlay`);
  expect(o.bounds).toEqual({ x: wa.x + wa.width - 440 - 16, y: wa.y + 16, width: 440, height: 150 });
  expect(o.focusable).toBe(false);
  expect((await page.evaluate(() => window.atsuitApp.overlayState())).on).toBe(true);

  // The app told the server what it is showing.
  const cookie = await adminCookie();
  const node = (await (await fetch(`${env.base}/api/fleet/nodes`, { headers: { Cookie: cookie } })).json()).find((n) => n.name === "TESTLAP1");
  await expect.poll(async () => {
    const ov = await (await fetch(`${env.base}/api/fleet/nodes/${node.id}/overlay`, { headers: { Cookie: cookie } })).json();
    return ov.state && ov.state.on && ov.state.target;
  }, { timeout: 10000 }).toBe(`${env.base}/timer/${rid}?view=overlay`);

  // Off stays off after a restart.
  await page.evaluate(() => window.atsuitApp.overlay({ on: false }));
  await app.close();
  app = await launch();
  await mainPage(app);
  await new Promise((r) => setTimeout(r, 2000));
  expect(await overlayWindows(app)).toEqual([]);
});

test("with the server down the app shows its own branded page, not a browser error", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "atsuit-node-off-"));
  fs.writeFileSync(path.join(dir, "node.json"), JSON.stringify({ server: "http://127.0.0.1:9", name: "OFFLINE-TEST", token_plain: "x" }));
  const args = [path.join(__dirname, "..")];
  if (process.platform === "linux") args.push("--no-sandbox");
  app = await electron.launch({ args, env: { ...process.env, ATSUIT_NODE_USERDATA: dir, ATSUIT_NODE_PRESET: path.join(dir, "none.json"), ATSUIT_NODE_NO_UPDATES: "1" } });
  let page = null;
  for (let i = 0; i < 50 && !page; i++) {
    page = app.windows().find((w) => w.url().includes("offline.html")) || null;
    if (!page) await new Promise((r) => setTimeout(r, 200));
  }
  expect(page).toBeTruthy();
  await expect(page.locator("h1")).toHaveText("Can't reach the AT-SUITE server");
  await expect(page.locator("#where")).toContainText("http://127.0.0.1:9");
  expect(await page.locator("img").evaluate((i) => i.naturalWidth)).toBeGreaterThan(0); // the gear loads
});
