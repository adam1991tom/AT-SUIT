// This laptop's settings: which AT-SUIT server it belongs to, its node token
// (encrypted with Windows DPAPI via safeStorage) and whether pop-ups are on.
// IT can pre-set the server and enrolment code for a silent roll-out in
// %ProgramData%\AT-SUIT\node.json: {"server": "http://10.100.70.101:8180", "enrol_code": "ABCD-1234-EF56"}
const fs = require("fs");
const path = require("path");
const { app, safeStorage } = require("electron");

// overlay: the last overlay settings ({ on, url, position, size, display,
// opacity, room_id }); an overlay left on comes back when the app starts.
const DEFAULTS = { server: "", name: "", token_enc: "", token_plain: "", popups: false, overlay: null };

function file() {
  return path.join(app.getPath("userData"), "node.json");
}

function presetFile() {
  if (process.env.ATSUIT_NODE_PRESET) return process.env.ATSUIT_NODE_PRESET;
  return path.join(process.env.ProgramData || "C:\\ProgramData", "AT-SUIT", "node.json");
}

function readJson(f) {
  try { return JSON.parse(fs.readFileSync(f, "utf8")); } catch (_) { return null; }
}

function load() {
  return { ...DEFAULTS, ...(readJson(file()) || {}) };
}

function save(conf) {
  fs.mkdirSync(path.dirname(file()), { recursive: true });
  const tmp = `${file()}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(conf, null, 2));
  fs.renameSync(tmp, file());
}

function preset() {
  const p = readJson(presetFile()) || {};
  return { server: normServer(p.server || ""), enrol_code: String(p.enrol_code || "").trim().toUpperCase(), name: String(p.name || "") };
}

function normServer(s) {
  s = String(s || "").trim().replace(/\/+$/, "");
  if (!s) return "";
  if (!/^https?:\/\//i.test(s)) s = `http://${s}`;
  try {
    const u = new URL(s);
    return `${u.protocol}//${u.host}`;
  } catch (_) {
    return "";
  }
}

function getToken(conf) {
  if (conf.token_enc && safeStorage.isEncryptionAvailable()) {
    try { return safeStorage.decryptString(Buffer.from(conf.token_enc, "base64")); } catch (_) { return ""; }
  }
  return conf.token_plain || "";
}

function setToken(conf, token) {
  if (safeStorage.isEncryptionAvailable()) {
    conf.token_enc = safeStorage.encryptString(token).toString("base64");
    conf.token_plain = "";
  } else {
    conf.token_plain = token;
    conf.token_enc = "";
  }
}

module.exports = { load, save, preset, normServer, getToken, setToken };
