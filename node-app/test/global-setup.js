// Start a throwaway AT-SUIT server with a site, rooms and two tech accounts.
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PORT = process.env.ATSUIT_TEST_PORT || "18180";
const BASE = `http://127.0.0.1:${PORT}`;

async function waitUp() {
  for (let i = 0; i < 120; i++) {
    try { if ((await fetch(`${BASE}/api/health`)).ok) return; } catch (_) {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("AT-SUIT server did not start");
}

async function call(method, url, body, cookie) {
  const r = await fetch(`${BASE}${url}`, {
    method,
    headers: { "Content-Type": "application/json", ...(cookie ? { Cookie: cookie } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`${method} ${url}: ${r.status} ${text}`);
  return { data: text ? JSON.parse(text) : null, cookie: (r.headers.get("set-cookie") || "").split(";")[0] };
}

module.exports = async () => {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "atsuit-test-"));
  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  const server = spawn(python, ["-m", "uvicorn", "atsuit.main:app", "--host", "127.0.0.1", "--port", PORT], {
    cwd: path.join(__dirname, "..", "..", "server"),
    env: { ...process.env, ATSUIT_DATA: data, ATSUIT_ASR: "0" },
    stdio: "ignore",
  });
  await waitUp();
  await call("POST", "/api/setup", { organisation: "Test", site_name: "Main", admin_username: "admin", admin_password: "correct-horse", rooms: ["CC", "HD", "RH"] });
  const { cookie } = await call("POST", "/api/auth/login", { username: "admin", password: "correct-horse" });
  await call("POST", "/api/admin/accounts", { username: "amy", display_name: "Amy", role: "tech", password: "amy-password-1" }, cookie);
  await call("POST", "/api/admin/accounts", { username: "ben", display_name: "Ben", role: "tech", password: "ben-password-1" }, cookie);
  const code = (await call("GET", "/api/fleet/enrolment", null, cookie)).data[0].enrol_code;
  fs.writeFileSync(path.join(os.tmpdir(), "atsuit-test.json"), JSON.stringify({ base: BASE, data, code, pid: server.pid }));
};
