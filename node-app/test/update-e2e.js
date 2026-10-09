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
  diagnose();
  throw new Error(`Timed out waiting for ${what}`);
}

// The installed app, from its uninstall entry: { version, dir }.
function ps(script) {
  return execFileSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], { encoding: "utf8" }).trim();
}
function installed() {
  let out = "";
  try {
    out = ps("Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | " +
      "Where-Object { $_.DisplayName -like 'AT-SUIT Node*' } | Select-Object -First 1 DisplayVersion, InstallLocation, UninstallString | ConvertTo-Json -Compress");
  } catch (_) { return null; }
  if (!out) return null;
  const k = JSON.parse(out);
  const dir = k.InstallLocation || path.dirname(String(k.UninstallString || "").replace(/^"([^"]+)".*/, "$1"));
  return { version: k.DisplayVersion, dir };
}

// What Windows knows, printed when a step times out.
function diagnose() {
  try { console.log(ps("Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | Select-Object DisplayName, DisplayVersion, InstallLocation | Format-List | Out-String")); } catch (e) { console.log(e.message); }
  try { console.log(ps("Get-ChildItem \"$env:LOCALAPPDATA\\Programs\" -Recurse -Depth 1 -Filter *.exe | Select-Object FullName | Out-String")); } catch (e) { console.log(e.message); }
  try { console.log(ps("Get-ChildItem \"$env:LOCALAPPDATA\" -Directory -Filter *updater* | ForEach-Object { Get-ChildItem $_.FullName -Recurse } | Select-Object FullName, Length | Out-String")); } catch (e) { console.log(e.message); }
  try { console.log(ps("Get-Process | Where-Object { $_.ProcessName -like 'AT-SUIT*' } | Select-Object Id, ProcessName, Path | Out-String")); } catch (e) { console.log(e.message); }
}

const exeIn = (dir, re) => fs.readdirSync(dir).find((f) => re.test(f));

(async () => {
  const setupA = exeIn(dirA, /^AT-SUIT-Node-Setup-.*\.exe$/);
  const B = /^version:\s*(\S+)/m.exec(fs.readFileSync(path.join(dirB, "latest.yml"), "utf8"))[1];
  const A = /Setup-(.+)\.exe$/.exec(setupA)[1];
  log(`A = ${A}, B = ${B}`);

  const data = fs.mkdtempSync(path.join(os.tmpdir(), "atsuit-update-"));
  const server = spawn(process.env.PYTHON || "python", ["tests/licensed_server.py", "--host", "127.0.0.1", "--port", PORT],
    { cwd: path.join(__dirname, "..", "..", "server"), env: { ...process.env, ATSUIT_DATA: data, ATSUIT_ASR: "0" }, stdio: "inherit" });
  let app = null;
  try {
    await until("the server", async () => (await fetch(`${BASE}/api/health`)).ok, 120);
    const licence_key = fs.readFileSync(path.join(data, "test-licence.txt"), "utf8");
    await call("POST", "/api/setup", { organisation: "Test", site_name: "Main", admin_username: "admin", admin_password: "correct-horse", rooms: ["CC"], licence_key });
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
    log("installing", setupA);
    execFileSync(path.join(dirA, setupA), ["/S"], { stdio: "inherit", timeout: 180000 });
    log("installer finished");
    const inst = await until("A to be installed", async () => { const i = installed(); return i && i.version === A && i; }, 120);
    log("installed", inst.version, "in", inst.dir);
    const exe = path.join(inst.dir, exeIn(inst.dir, /^AT-SUIT Node\.exe$/i) || "AT-SUIT Node.exe");

    const nodeVersion = async () => {
      const nodes = (await call("GET", "/api/fleet/nodes", null, cookie)).data;
      // Node names are stored normalised (e.g. upper case), so compare loosely.
      const n = nodes.find((x) => String(x.name).toLowerCase() === "update-test");
      return n ? n.version || "enrolled" : null;
    };
    // As a tech would: start A and leave it. It checks the server as it starts, finds B, downloads
    // it and (update on launch is on by default) installs it and starts B by itself.
    const env = { ...process.env, ATSUIT_NODE_UPDATE_CHECK_S: "5" };
    app = spawn(exe, [], { env, detached: true, stdio: "ignore" });
    // A may download B and restart before its page first reports in, so enrolment is enough here.
    await until("A to enrol", async () => !!(await nodeVersion()), 120);
    log("A enrolled, reporting", await nodeVersion());

    await until("B to be installed", async () => installed()?.version === B, 300);
    log("updated to", B);
    // Nobody starts it again: the update restarts the app.
    await until("B to start by itself and report in", async () => (await nodeVersion()) === `app-${B}`, 180);
    log("PASS: the app updated itself on launch from", A, "to", B, ", restarted and reports", B);
  } finally {
    try { execFileSync("taskkill", ["/IM", "AT-SUIT Node.exe", "/F", "/T"], { stdio: "ignore" }); } catch (_) {}
    server.kill();
  }
})().then(() => process.exit(0), (e) => { console.error("FAIL:", e.message); process.exit(1); });
