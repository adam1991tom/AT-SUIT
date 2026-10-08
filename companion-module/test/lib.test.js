const test = require("node:test"), assert = require("node:assert");
const { PRESETS, baseUrl, fmt, request } = require("../lib");

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
