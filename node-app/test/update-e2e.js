// The Windows app updates itself from the venue server, end to end (CI on Windows):
// install version A, publish version B on a server, start A, and check that it
// downloads B, installs it when it quits, and comes back reporting B.
//   node test/update-e2e.js <dir with installer A> <dir with release B>
const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const [dirA, dirB] = process.argv.slice(2).map((d) => path.resolve(d));
const PORT = process.env.ATSUIT_TEST_PORT || "18190";
const BASE = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function call(method, url, body, cookie) {
  const form = body instanceof FormData;
  const r = await fetch(`${BASE}${url}`, { method, body: form ? body : body ? JSON.stringify(body) : undefined,
    headers: { ...(form ? {} : { "Content-Type": "application/json" }), ...(cookie ? { Cookie: cookie } : {}) } });
  const text = await r.text();
  if (!r.ok) throw new Error(`${method} ${url}: ${r.status} ${text}`);
  return { data: text ? JSON.parse(text) : null, cookie: (r.headers.get("set-cookie") || "").split(";")[0] };
}

async function until(what, fn, seconds) {
  for (let i = 0; i < seconds; i++) {
    const v = await fn().catch(() => null);
    if (v) return v;
    await sleep(1000);
  }
  throw new Error(`Timed out waiting for ${what}`);
}

// The installed app, from its uninstall entry: { version, dir }.
function installed() {
  let out = "";
  try { out = execFileSync("reg", ["query", "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall", "/s", "/f", "AT-SUIT Node", "/d"], { encoding: "utf8" }); }
  catch (_) { return null; }
  const key = out.split(/\r?\n\r?\n/).find((b) => /DisplayName\s+REG_SZ\s+AT-SUIT Node/.test(b));
  if (!key) return null;
  const val = (n) => (key.match(new RegExp(`${n}\\s+REG_\\w+\\s+(.*)`)) || [])[1]?.trim();
  let dir = val("InstallLocation");
  if (!dir) dir = path.dirname((val("UninstallString") || "").replace(/^"([^"]+)".*/, "$1"));
  return { version: val("DisplayVersion"), dir };
}

const exeIn = (dir, re) => fs.readdirSync(dir).find((f) => re.test(f));

(async () => {
  const setupA = exeIn(dirA, /^AT-SUIT-Node-Setup-.*\.exe$/);
  const B = /^version:\s*(\S+)/m.exec(fs.readFileSync(path.join(dirB, "latest.yml"), "utf8"))[1];
  const A = /Setup-(.+)\.exe$/.exec(setupA)[1];
  log(`A = ${A}, B = ${B}`);

  const data = fs.mkdtempSync(path.join(os.tmpdir(), "atsuit-update-"));
  const server = spawn(process.env.PYTHON || "python", ["-m", "uvicorn", "atsuit.main:app", "--host", "127.0.0.1", "--port", PORT],
    { cwd: path.join(__dirname, "..", "..", "server"), env: { ...process.env, ATSUIT_DATA: data, ATSUIT_ASR: "0" }, stdio: "inherit" });
  let app = null;
  try {
    await until("the server", async () => (await fetch(`${BASE}/api/health`)).ok, 120);
    await call("POST", "/api/setup", { organisation: "Test", site_name: "Main", admin_username: "admin", admin_password: "correct-horse", rooms: ["CC"] });
    const { cookie } = await call("POST", "/api/auth/login", { username: "admin", password: "correct-horse" });
    const code = (await call("GET", "/api/fleet/enrolment", null, cookie)).data[0].enrol_code;

    // Publish B, exactly as an admin does from Laptops & screens → Add laptops.
    const form = new FormData();
    for (const f of fs.readdirSync(dirB).filter((f) => /\.(exe|blockmap)$|^latest\.yml$/.test(f)))
      form.append("files", new Blob([fs.readFileSync(path.join(dirB, f))]), f);
    const rel = (await call("POST", "/api/fleet/app", form, cookie)).data;
    if (rel.version !== B || !rel.ready) throw new Error(`publishing B failed: ${JSON.stringify(rel)}`);
    log("published", B);

    // Install A silently, set up the way IT does it: node.json in ProgramData.
    const pre = path.join(process.env.ProgramData || "C:\\ProgramData", "AT-SUIT");
    fs.mkdirSync(pre, { recursive: true });
    fs.writeFileSync(path.join(pre, "node.json"), JSON.stringify({ server: BASE, enrol_code: code, name: "update-test" }));
    execFileSync(path.join(dirA, setupA), ["/S"], { stdio: "inherit" });
    const inst = await until("A to be installed", async () => { const i = installed(); return i && i.version === A && i; }, 120);
    log("installed", inst.version, "in", inst.dir);
    const exe = path.join(inst.dir, exeIn(inst.dir, /^AT-SUIT Node\.exe$/i) || "AT-SUIT Node.exe");

    const nodeVersion = async () => {
      const nodes = (await call("GET", "/api/fleet/nodes", null, cookie)).data;
      return (nodes.find((n) => n.name === "update-test") || {}).version;
    };
    const env = { ...process.env, ATSUIT_NODE_UPDATE_CHECK_S: "5", ATSUIT_NODE_QUIT_TO_UPDATE: "1" };
    app = spawn(exe, [], { env, detached: true, stdio: "ignore" });
    await until("A to enrol and report in", async () => (await nodeVersion()) === `app-${A}`, 120);
    log("A is running and enrolled");

    // A finds B, downloads it, quits and installs it.
    await until("B to be installed", async () => installed()?.version === B, 300);
    log("updated to", B);

    app = spawn(exe, [], { env: { ...process.env, ATSUIT_NODE_NO_UPDATES: "1" }, detached: true, stdio: "ignore" });
    await until("B to report in", async () => (await nodeVersion()) === `app-${B}`, 120);
    log("PASS: the app updated itself from", A, "to", B, "and reports", B);
  } finally {
    try { execFileSync("taskkill", ["/IM", "AT-SUIT Node.exe", "/F", "/T"], { stdio: "ignore" }); } catch (_) {}
    server.kill();
  }
})().then(() => process.exit(0), (e) => { console.error("FAIL:", e.message); process.exit(1); });
