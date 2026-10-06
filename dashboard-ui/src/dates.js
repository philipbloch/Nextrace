export function dateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function localDateStart(value) {
  if (!value) return null;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return dateInputValue(date) === value ? date : null;
}

export function presetDateRange(preset, now = new Date()) {
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (preset === "yesterday") day.setDate(day.getDate() - 1);
  return { start: dateInputValue(day), end: dateInputValue(day) };
}

export function dateRangeWindow(mode, startValue, endValue, now = new Date()) {
  const preset = mode === "today" || mode === "yesterday";
  const range = preset ? presetDateRange(mode, now) : { start: startValue, end: endValue };
  const start = localDateStart(range.start);
  let end = localDateStart(range.end);
  if (start && end && end < start) end = start;
  // Calendar days can contain 23 or 25 hours when daylight saving time changes.
  const until = end ? new Date(end.getFullYear(), end.getMonth(), end.getDate() + 1) : null;
  return {
    since: start ? start.getTime() / 1000 : null,
    until: until ? until.getTime() / 1000 : null,
    start: start ? dateInputValue(start) : "",
    end: end ? dateInputValue(end) : "",
    label: preset
      ? `Showing ${mode === "today" ? "Today" : "Yesterday"}`
      : `Showing ${start ? start.toLocaleDateString() : "Beginning"} to ${end ? end.toLocaleDateString() : "Now"}`,
  };
}
