// End-to-end: the real app against a real AT-SUIT server.
const { _electron: electron, test, expect } = require("@playwright/test");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

test.describe.configure({ mode: "serial" });

const env = JSON.parse(fs.readFileSync(path.join(os.tmpdir(), "atsuit-test.json"), "utf8"));
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "atsuit-node-"));
const preset = path.join(userData, "preset.json");
let app;

async function launch() {
  const args = [path.join(__dirname, "..")];
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

async function start(page, { name, room, mode }) {
  await expect(page.locator("#start")).toBeVisible();
  if (name !== undefined) await page.fill("#startForm [name=operator]", name);
  if (room) await page.click(`#startRooms [data-room] >> text=${room}`);
  if (mode) await page.check(`#startForm [name=mode][value=${mode}]`);
  await page.click("#startForm button.primary");
  await expect(page.locator("#ws")).toBeVisible();
}

const conf = () => JSON.parse(fs.readFileSync(path.join(userData, "node.json"), "utf8"));

test("after enrolment the tech gives their name, the room and Main or Backup; no password", async () => {
  app = await launch();
  const page = await mainPage(app);
  expect(app.windows().some((w) => w.url().includes("setup.html"))).toBeFalsy();
  await expect(page.locator("#start h1")).toHaveText("Start on TESTLAP1");
  await expect(page.locator("#login")).toBeHidden();
  if (process.env.ATSUIT_SHOTS) await page.screenshot({ path: path.join(process.env.ATSUIT_SHOTS, "start.png") });
  await start(page, { name: "Amy", room: "HD", mode: "main" });
  await expect(page.locator("#room option:checked")).toHaveText("HD");
  await expect(page.locator("#who")).toHaveText("Amy on TESTLAP1");
  await expect(page.locator("#modePill")).toHaveText("Main PC");
  expect(conf().popups).toBe(false);
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
  // The next heartbeat notices and shows the start screen, remembering the name and Main/Backup.
  await expect(page.locator("#start")).toBeVisible({ timeout: 20000 });
  expect(app.windows().some((w) => w.url().includes("setup.html"))).toBeFalsy();
  await expect(page.locator("#startForm [name=operator]")).toHaveValue("Amy");
  await expect(page.locator("#startForm [name=mode][value=backup]")).toBeChecked();
  await start(page, { name: "Ben", room: "CC" });
  await expect(page.locator("#room option:checked")).toHaveText("CC");
  await expect(page.locator("#who")).toHaveText("Ben on TESTLAP1");
  // Sign out at the end of the day: back to the start screen, and nothing pops up.
  await page.click("#out");
  await expect(page.locator("#start")).toBeVisible();
  expect(conf().popups).toBe(false);
});
