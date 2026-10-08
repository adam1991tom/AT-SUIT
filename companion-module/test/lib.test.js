const test = require("node:test"), assert = require("node:assert");
const { PRESETS, QUICK_MESSAGES, baseUrl, fmt, request, secondaryText } = require("../lib");

test("presets are 3 to 60 in the agreed steps", () => assert.deepStrictEqual(PRESETS, [3, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]));
test("address", () => {
  assert.strictEqual(baseUrl({ host: "http://10.1.1.1/", port: 8180 }), "http://10.1.1.1:8180");
  assert.strictEqual(baseUrl({ host: "x.local", https: true }), "https://x.local:443");
});
test("time format", () => { assert.strictEqual(fmt(65000), "01:05"); assert.strictEqual(fmt(3725000), "1:02:05"); assert.strictEqual(fmt(-4000), "-00:04"); });
test("requests", () => {
  assert.deepStrictEqual(request("add", { minutes: -1 }, 3), ["/api/timers/3/add", { delta_ms: -60000 }]);
  assert.deepStrictEqual(request("blink", { mode: "toggle" }, 3), ["/api/timers/3/blink/toggle", {}]);
  assert.strictEqual(request("overlay", { node: 7, on: "off" }, 3)[2], "PUT");
  assert.throws(() => request("nope", {}, 3));
});
test("quick messages show, and hide when already on stage", () => {
  assert.ok(QUICK_MESSAGES.includes("Stand behind the mic"));
  assert.deepStrictEqual(request("quick_message", { text: "Last question", blink: true }, 3), ["/api/timers/3/message/show", { text: "Last question", blink: true }]);
  assert.deepStrictEqual(request("quick_message", { text: "Last question" }, 3, { message_visible: true, message: "Last question" }), ["/api/timers/3/message/hide", {}]);
  assert.strictEqual(request("quick_message", { text: "Last question" }, 3, { message_visible: true, message: "Other" })[0], "/api/timers/3/message/show");
});
test("second line", () => {
  assert.deepStrictEqual(request("secondary_timer", { minutes: 5 }, 3), ["/api/timers/3/secondary/timer", { minutes: 5, start: true }]);
  assert.deepStrictEqual(request("secondary_text", { text: "Q&A" }, 3), ["/api/timers/3/secondary/text", { text: "Q&A" }]);
  assert.deepStrictEqual(request("secondary_control", { cmd: "hide" }, 3), ["/api/timers/3/secondary/hide", {}]);
  assert.throws(() => request("secondary_control", { cmd: "explode" }, 3));
  assert.deepStrictEqual(request("secondary_add", { minutes: -1 }, 3), ["/api/timers/3/secondary/add", { delta_ms: -60000 }]);
  assert.strictEqual(secondaryText({ secondary: { visible: true, mode: "timer", remaining_ms: 65000, running: false } }), "01:05");
  assert.strictEqual(secondaryText({ secondary: { visible: true, mode: "text", text: "Q&A" } }), "Q&A");
  assert.strictEqual(secondaryText({ secondary: { visible: false, mode: "text", text: "Q&A" } }), "");
});
