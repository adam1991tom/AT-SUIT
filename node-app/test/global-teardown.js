const fs = require("fs");
const os = require("os");
const path = require("path");

module.exports = async () => {
  const f = path.join(os.tmpdir(), "atsuit-test.json");
  try {
    const { pid } = JSON.parse(fs.readFileSync(f, "utf8"));
    process.kill(pid);
  } catch (_) {}
};
