// Pure helpers, kept apart from the Companion plumbing so they can be tested.
const PRESETS = [3, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60];

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

// The request for each action: [path, body]. Room is the room id.
function request(action, o, room) {
  const t = `/api/timers/${room}`;
  switch (action) {
    case "preset": return [`${t}/preset`, { minutes: Number(o.minutes), title: o.title || undefined, start: o.start !== false }];
    case "add": return [`${t}/add`, { delta_ms: Math.round(Number(o.minutes) * 60000) }];
    case "control": return [`${t}/${o.cmd}`, {}];
    case "blink": case "clock": case "blackout": return [`${t}/${action}/${o.mode}`, {}];
    case "message": return [`${t}/message/show`, { text: o.text, blink: !!o.blink }];
    case "message_hide": return [`${t}/message/hide`, {}];
    case "overlay": return [`/api/fleet/nodes/${o.node}/overlay`, o.on === "on" ? { on: true, ...(o.url ? { url: o.url } : {}) } : { on: false }, "PUT"];
    case "caption_test": return [`/api/captions/${room}/test`, { text: o.text, final: true }];
    case "caption_clear": return [`/api/captions/${room}/clear`, {}];
    default: throw new Error("Unknown action " + action);
  }
}

module.exports = { PRESETS, baseUrl, fmt, request };
