import assert from "node:assert/strict";
import test from "node:test";
import { dateRangeWindow, localDateStart, presetDateRange } from "./dates.js";

process.env.TZ = "America/Vancouver";

test("date filters cover local calendar days across DST changes", () => {
  for (const [day, hours] of [
    ["2026-03-08", 23],
    ["2026-11-01", 25],
    ["2026-10-05", 24],
  ]) {
    const range = dateRangeWindow("custom", day, day);
    assert.equal((range.until - range.since) / 3600, hours);
    assert.equal(new Date(range.since * 1000).getHours(), 0);
    assert.equal(new Date(range.until * 1000).getHours(), 0);
  }
});

test("presets refresh across midnight and custom ranges clamp reversed endpoints", () => {
  assert.deepEqual(presetDateRange("yesterday", new Date(2026, 0, 1)), {
    start: "2025-12-31",
    end: "2025-12-31",
  });
  const before = dateRangeWindow("today", "", "", new Date(2026, 9, 5, 23));
  const after = dateRangeWindow("today", "", "", new Date(2026, 9, 6, 0));
  assert.equal(after.since, before.until);
  const range = dateRangeWindow("custom", "2026-10-05", "2026-10-01");
  assert.equal(range.end, "2026-10-05");
  assert.equal(localDateStart("2026-02-31"), null);
  assert.equal(dateRangeWindow("custom", "", "").until, null);
});
