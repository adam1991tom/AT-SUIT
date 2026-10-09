// Pure helpers, kept apart from the Companion plumbing so they can be tested.
const PRESETS = [3, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60];
// The quick messages AT-SUIT ships with (Admin can change the workspace's list).
const QUICK_MESSAGES = ["Please wrap up", "5 minutes", "2 minutes", "1 minute", "Time is up", "Please come off stage",
  "Stand behind the mic", "Please speak into the mic", "Mic closer please", "Please slow down", "Louder please",
  "There is an issue, please wait", "Slides are coming", "Questions from the room next", "Last question",
  "Please turn your phone off", "Please repeat the question", "Look at the camera"];
const SECONDARY_CMDS = ["toggle", "start", "pause", "reset", "show", "hide"];

function baseUrl(config) {
  const host = String(config.host || "").trim().replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const scheme = config.https ? "https" : "http";
  return `${scheme}://${host}:${config.port || (config.https ? 443 : 8180)}`;
}

// "mm:ss" (or "h:mm:ss" past an hour) from milliseconds. Overtime gets a minus.
function fmt(ms) {
  const neg = ms < 0, s = Math.floor(Math.abs(ms) / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const p = (n) => String(n).padStart(2, "0");
  return (neg ? "-" : "") + (h ? `${h}:${p(m)}:${p(r)}` : `${p(m)}:${p(r)}`);
}

// The request for each action: [path, body]. Room is the room id; state is the
// room's last timer state (a quick message on stage is hidden by a second press).
function request(action, o, room, state) {
  const t = `/api/timers/${room}`;
  switch (action) {
    case "preset": return [`${t}/preset`, { minutes: Number(o.minutes), title: o.title || undefined, start: o.start !== false }];
    case "add": return [`${t}/add`, { delta_ms: Math.round(Number(o.minutes) * 60000) }];
    case "control": return [`${t}/${o.cmd}`, {}];
    case "blink": case "clock": case "blackout": return [`${t}/${action}/${o.mode}`, {}];
    case "message": return [`${t}/message/show`, { text: o.text, blink: !!o.blink }];
    case "message_hide": return [`${t}/message/hide`, {}];
    case "quick_message": return state && state.message_visible && state.message === o.text ? [`${t}/message/hide`, {}] : [`${t}/message/show`, { text: o.text, blink: !!o.blink }];
    case "secondary_text": return [`${t}/secondary/text`, { text: o.text }];
    case "secondary_timer": return [`${t}/secondary/timer`, { minutes: Number(o.minutes), start: o.start !== false }];
    case "secondary_control": if (!SECONDARY_CMDS.includes(o.cmd)) throw new Error("Unknown command " + o.cmd); return [`${t}/secondary/${o.cmd}`, {}];
    case "secondary_add": return [`${t}/secondary/add`, { delta_ms: Math.round(Number(o.minutes) * 60000) }];
    case "overlay": return [`/api/fleet/nodes/${o.node}/overlay`, o.on === "on" ? { on: true, ...(o.url ? { url: o.url } : {}) } : { on: false }, "PUT"];
    case "caption_test": return [`/api/captions/${room}/test`, { text: o.text, final: true }];
    case "caption_clear": return [`/api/captions/${room}/clear`, {}];
    default: throw new Error("Unknown action " + action);
  }
}

// What the second line under the timer shows now: the text, or its countdown.
function secondaryText(state, now = Date.now()) {
  const x = state && state.secondary;
  if (!x || !x.visible) return "";
  if (x.mode !== "timer") return x.text || "";
  return fmt(x.running && state._rx ? x.remaining_ms - (now - state._rx) : x.remaining_ms);
}

// Danger only for a count-down that is running or paused and under its danger time
// (a stopped timer has 0 left, which isn't danger).
function inDanger(st) {
  if (!st || st.remaining_ms == null || !["play", "pause"].includes(st.playback)) return false;
  if (st.timer_type && st.timer_type !== "count-down") return false;
  return st.remaining_ms <= (st.danger_ms || 0);
}

// The time left for a button: --:-- when nothing is loaded or it has been stopped.
const timeLeft = (st) => (!st || st.remaining_ms == null || !["play", "pause", "armed"].includes(st.playback) ? "--:--" : fmt(st.remaining_ms));

module.exports = { PRESETS, QUICK_MESSAGES, SECONDARY_CMDS, baseUrl, fmt, inDanger, request, secondaryText, timeLeft };
