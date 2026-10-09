// Runs the real app against a real AT-SUIT server (started in global-setup).
module.exports = {
  testDir: "test",
  timeout: 90000,
  workers: 1,
  retries: 0,
  globalSetup: require.resolve("./test/global-setup.js"),
  globalTeardown: require.resolve("./test/global-teardown.js"),
  reporter: [["list"]],
};
