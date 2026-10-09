const { InstanceBase, InstanceStatus, Regex, combineRgb, runEntrypoint } = require("@companion-module/base");
const { PRESETS, QUICK_MESSAGES, SECONDARY_CMDS, baseUrl, inDanger, request, secondaryText, timeLeft } = require("./lib");

const NAVY = combineRgb(11, 16, 32), ORANGE = combineRgb(255, 122, 26), RED = combineRgb(200, 30, 30), GREEN = combineRgb(30, 140, 60), WHITE = combineRgb(255, 255, 255);

class AtSuit extends InstanceBase {
  async init(config) {
    this.config = config; this.state = {};
    this.setActionDefinitions(this.actions()); this.setFeedbackDefinitions(this.feedbacks());
    this.setVariableDefinitions([
      { variableId: "time_left", name: "Time left (mm:ss)" }, { variableId: "running", name: "Running (yes/no)" },
      { variableId: "title", name: "Timer title" }, { variableId: "message", name: "Stage message" }, { variableId: "cue", name: "Cue loaded" },
      { variableId: "secondary", name: "Second line under the timer (text or mm:ss)" },
    ]);
    this.setPresetDefinitions(this.presets());
    this.startPolling();
  }
  async destroy() { clearInterval(this.poll); }
  async configUpdated(config) { this.config = config; this.startPolling(); }

  getConfigFields() {
    return [
      { type: "textinput", id: "host", label: "AT-SUIT address", width: 8, default: "10.100.70.100" },
      { type: "number", id: "port", label: "Port", width: 4, default: 8180, min: 1, max: 65535 },
      { type: "checkbox", id: "https", label: "Use https", width: 4, default: false },
      { type: "textinput", id: "key", label: "API key (Admin, API keys)", width: 12, regex: Regex.SOMETHING },
      { type: "number", id: "room", label: "Room id (Admin, Sites & rooms)", width: 4, default: 1, min: 1, max: 100000 },
    ];
  }

  async call(action, opts) {
    const [path, body, method] = request(action, opts, this.config.room, this.state);
    const r = await fetch(baseUrl(this.config) + path, {
      method: method || "POST", headers: { "X-API-Key": this.config.key || "", "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) { this.log("error", `${action}: ${r.status} ${(await r.text()).slice(0, 120)}`); this.updateStatus(InstanceStatus.UnknownWarning, `${r.status}`); }
    this.pollOnce();
  }

  startPolling() {
    clearInterval(this.poll);
    if (!this.config.host) return this.updateStatus(InstanceStatus.BadConfig, "No address");
    this.pollOnce(); this.poll = setInterval(() => this.pollOnce(), 1000);
  }
  async pollOnce() {
    try {
      const r = await fetch(`${baseUrl(this.config)}/api/timers/${this.config.room}`, { signal: AbortSignal.timeout(2500) });
      if (!r.ok) throw new Error(String(r.status));
      this.state = await r.json(); this.state._rx = Date.now(); this.updateStatus(InstanceStatus.Ok);
      this.setVariableValues({ time_left: timeLeft(this.state), running: this.state.running ? "yes" : "no", title: this.state.title || "", message: this.state.message || "", cue: this.state.cue ? this.state.cue.title : "", secondary: secondaryText(this.state) });
      this.checkFeedbacks();
    } catch (e) { this.updateStatus(InstanceStatus.ConnectionFailure, e.message); }
  }

  actions() {
    const mode = { type: "dropdown", id: "mode", label: "Mode", default: "toggle", choices: [{ id: "on", label: "On" }, { id: "off", label: "Off" }, { id: "toggle", label: "Toggle" }] };
    const run = (name) => async (e) => this.call(name, e.options);
    return {
      preset: { name: "Set the timer to N minutes", options: [{ type: "number", id: "minutes", label: "Minutes", default: 5, min: 0.1, max: 600 }, { type: "textinput", id: "title", label: "Title (optional)" }, { type: "checkbox", id: "start", label: "Start it", default: true }], callback: run("preset") },
      add: { name: "Add or take off minutes", options: [{ type: "number", id: "minutes", label: "Minutes (minus to take off)", default: 1, min: -600, max: 600 }], callback: run("add") },
      control: { name: "Timer control", options: [{ type: "dropdown", id: "cmd", label: "Command", default: "toggle", choices: ["toggle", "start", "pause", "reset", "stop", "go", "next", "previous"].map((id) => ({ id, label: id })) }], callback: run("control") },
      blink: { name: "Blink", options: [mode], callback: run("blink") },
      clock: { name: "Clock instead of the timer", options: [mode], callback: run("clock") },
      blackout: { name: "Blackout", options: [mode], callback: run("blackout") },
      message: { name: "Show a stage message", options: [{ type: "textinput", id: "text", label: "Text", default: "Please wrap up" }, { type: "checkbox", id: "blink", label: "Flash it", default: false }], callback: run("message") },
      message_hide: { name: "Hide the stage message", options: [], callback: run("message_hide") },
      quick_message: { name: "Quick message (press again to hide)", options: [{ type: "dropdown", id: "text", label: "Message", default: QUICK_MESSAGES[0], allowCustom: true, choices: QUICK_MESSAGES.map((m) => ({ id: m, label: m })) }, { type: "checkbox", id: "blink", label: "Flash it", default: false }], callback: run("quick_message") },
      secondary_text: { name: "Second line: show a text", options: [{ type: "textinput", id: "text", label: "Text", default: "Q&A next" }], callback: run("secondary_text") },
      secondary_timer: { name: "Second line: countdown of N minutes", options: [{ type: "number", id: "minutes", label: "Minutes", default: 5, min: 0.1, max: 600 }, { type: "checkbox", id: "start", label: "Start it", default: true }], callback: run("secondary_timer") },
      secondary_control: { name: "Second line: control", options: [{ type: "dropdown", id: "cmd", label: "Command", default: "toggle", choices: SECONDARY_CMDS.map((id) => ({ id, label: id })) }], callback: run("secondary_control") },
      secondary_add: { name: "Second line: add or take off minutes", options: [{ type: "number", id: "minutes", label: "Minutes (minus to take off)", default: 1, min: -600, max: 600 }], callback: run("secondary_add") },
      overlay: { name: "AT Overlay on a laptop", options: [{ type: "number", id: "node", label: "Laptop (node) id", default: 1, min: 1, max: 100000 }, { type: "dropdown", id: "on", label: "State", default: "on", choices: [{ id: "on", label: "On" }, { id: "off", label: "Off" }] }, { type: "textinput", id: "url", label: "Web page (empty = the room timer)" }], callback: run("overlay") },
      caption_test: { name: "Send a test caption", options: [{ type: "textinput", id: "text", label: "Text", default: "Test caption" }], callback: run("caption_test") },
      caption_clear: { name: "Clear the caption screens", options: [], callback: run("caption_clear") },
    };
  }

  feedbacks() {
    const flag = (name, key) => ({ type: "boolean", name, defaultStyle: { bgcolor: RED, color: WHITE }, options: [], callback: () => !!this.state[key] });
    return {
      running: { type: "boolean", name: "Timer is running", defaultStyle: { bgcolor: GREEN, color: WHITE }, options: [], callback: () => !!this.state.running },
      danger: { type: "boolean", name: "Timer is in the danger zone", defaultStyle: { bgcolor: RED, color: WHITE }, options: [], callback: () => inDanger(this.state) },
      blink: flag("Blink is on", "message_blink"), clock: flag("Clock is showing", "show_clock"), blackout: flag("Blackout is on", "blackout"),
      message: flag("A stage message is showing", "message_visible"),
      quick_message: { type: "boolean", name: "This message is on stage", defaultStyle: { bgcolor: ORANGE, color: NAVY }, options: [{ type: "textinput", id: "text", label: "Message", default: QUICK_MESSAGES[0] }],
        callback: (fb) => !!this.state.message_visible && this.state.message === fb.options.text },
      secondary: { type: "boolean", name: "The second line is showing", defaultStyle: { bgcolor: ORANGE, color: NAVY }, options: [], callback: () => !!(this.state.secondary && this.state.secondary.visible) },
    };
  }

  presets() {
    const out = {}, base = { size: "14", color: WHITE, bgcolor: NAVY };
    for (const m of PRESETS) out[`p${m}`] = { type: "button", category: "Timer presets", name: `${m} min`, style: { ...base, text: `${m}\\nmin` }, steps: [{ down: [{ actionId: "preset", options: { minutes: m, title: "", start: true } }], up: [] }], feedbacks: [] };
    const btn = (cat, id, text, actionId, options, fb) => { out[id] = { type: "button", category: cat, name: text, style: { ...base, text }, steps: [{ down: [{ actionId, options }], up: [] }], feedbacks: fb || [] }; };
    btn("Timer", "plus", "+1\\nmin", "add", { minutes: 1 }); btn("Timer", "minus", "-1\\nmin", "add", { minutes: -1 });
    btn("Timer", "toggle", "$(at-suit:time_left)", "control", { cmd: "toggle" }, [{ feedbackId: "running", options: {}, style: { bgcolor: GREEN, color: WHITE } }, { feedbackId: "danger", options: {}, style: { bgcolor: RED, color: WHITE } }]);
    btn("Timer", "reset", "Reset", "control", { cmd: "reset" }); btn("Timer", "go", "GO", "control", { cmd: "go" });
    btn("Display", "blink", "Blink", "blink", { mode: "toggle" }, [{ feedbackId: "blink", options: {}, style: { bgcolor: ORANGE, color: NAVY } }]);
    btn("Display", "clock", "Clock", "clock", { mode: "toggle" }, [{ feedbackId: "clock", options: {}, style: { bgcolor: ORANGE, color: NAVY } }]);
    btn("Display", "blackout", "Blackout", "blackout", { mode: "toggle" }, [{ feedbackId: "blackout", options: {}, style: { bgcolor: RED, color: WHITE } }]);
    btn("Messages", "wrap", "Wrap up", "message", { text: "Please wrap up", blink: false }); btn("Messages", "five", "5 minutes", "message", { text: "5 minutes", blink: false });
    btn("Messages", "hide", "Hide\\nmessage", "message_hide", {}, [{ feedbackId: "message", options: {}, style: { bgcolor: ORANGE, color: NAVY } }]);
    QUICK_MESSAGES.forEach((m, i) => btn("Quick messages", `qm${i}`, m, "quick_message", { text: m, blink: false }, [{ feedbackId: "quick_message", options: { text: m }, style: { bgcolor: ORANGE, color: NAVY } }]));
    for (const m of [1, 2, 3, 5, 10]) btn("Second line", `s${m}`, `2nd\\n${m} min`, "secondary_timer", { minutes: m, start: true });
    btn("Second line", "stoggle", "$(at-suit:secondary)", "secondary_control", { cmd: "toggle" }, [{ feedbackId: "secondary", options: {}, style: { bgcolor: ORANGE, color: NAVY } }]);
    btn("Second line", "shide", "Hide\\n2nd line", "secondary_control", { cmd: "hide" }); btn("Second line", "sshow", "Show\\n2nd line", "secondary_control", { cmd: "show" });
    btn("Second line", "sqa", "Q&A next", "secondary_text", { text: "Q&A next" });
    return out;
  }
}

runEntrypoint(AtSuit, []);
