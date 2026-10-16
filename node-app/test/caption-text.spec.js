// The laptop tidies caption text exactly as the server does (server/atsuit/asr.py tidy).
const { test, expect } = require("@playwright/test");
const { tidy, caseWords, isFillerSpam } = require("../src/caption-text");

test("caption text is tidied on the laptop the same way as on the server", () => {
  const keep = caseWords(["Harrogate", "iPhone launch"]);
  expect(tidy("THE N H S IN THE U K", {})).toBe("THE NHS IN THE UK");
  expect(tidy("THE N H S IN THE U K", { join_acronyms: false })).toBe("THE N H S IN THE U K");
  expect(tidy("THE N H S IN THE U K", { sentence_case: true }, keep)).toBe("The NHS in the UK");
  expect(tidy("I'M GOING TO HARROGATE WITH THE B B C", { sentence_case: true }, keep)).toBe("I'm going to Harrogate with the BBC");
  expect(tidy("THE NEW IPHONE", { sentence_case: true }, keep)).toBe("The new iPhone");
  expect(isFillerSpam("um uh UM")).toBe(true);
  expect(isFillerSpam("um hello")).toBe(false);
});
