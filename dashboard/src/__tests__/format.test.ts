import { pct, prettyArgs, secs, usd, when } from "@/lib/format";

test("percentages and missing values", () => {
  expect(pct(0.756)).toBe("76%");
  expect(pct(null)).toBe("–");
  expect(pct(0)).toBe("0%");
});

test("durations", () => {
  expect(secs(4.25)).toBe("4.3s");
  expect(secs(125)).toBe("2m 5s");
});

test("costs", () => {
  expect(usd(0)).toBe("$0");
  expect(usd(0.0042)).toBe("$0.0042");
  expect(usd(1.5)).toBe("$1.50");
  expect(usd(null)).toBe("–");
});

test("relative times", () => {
  const now = new Date("2026-10-22T12:00:00Z");
  expect(when("2026-10-22T11:59:50Z", now)).toBe("just now");
  expect(when("2026-10-22T11:15:00Z", now)).toBe("45m ago");
  expect(when("2026-10-22T02:00:00Z", now)).toBe("10h ago");
});

test("tool arguments", () => {
  expect(prettyArgs('{"element": 3}')).toBe("element: 3");
  expect(prettyArgs('{"url": "/a", "n": [1]}')).toBe("url: /a, n: [1]");
  expect(prettyArgs("not json")).toBe("not json");
  expect(prettyArgs(null)).toBe("");
});
