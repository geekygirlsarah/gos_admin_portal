import { readFileSync } from "node:fs";
import { join } from "node:path";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const template = readFileSync(join(process.cwd(), "templates", "calendar_feeds", "calendar_view.html"), "utf8");

function setup() {
  const dom = new JSDOM(`<div id="calendar"></div><div id="eventDetailModal"></div>
    <span id="eventDetailTitle"></span><span id="eventDetailFeed"></span><span id="eventDetailWhen"></span>
    <div id="eventDetailLocationRow"></div><span id="eventDetailLocation"></span>
    <div id="eventDetailDescRow"></div><span id="eventDetailDescription"></span>
    <a id="eventDetailEditBtn"></a><form id="eventDetailDeleteForm"></form>
    <button id="eventDetailDeleteBtn"></button><a id="eventDetailOccurrenceEditBtn"></a>
    <a id="eventDetailOccurrenceDeleteBtn"></a><a id="eventDetailGCalBtn"></a>
    <form id="regenerateTokenForm"></form>`, { runScripts: "outside-only" });
  const show = vi.fn();
  let options;
  dom.window.bootstrap = { Modal: class { show() { show(); } } };
  dom.window.FullCalendar = { Calendar: class {
    constructor(el, opts) { options = opts; }
    render() {}
  } };
  const script = template.match(/<script nonce="\{\{ csp_nonce \}\}">([\s\S]*?)<\/script>/)[1]
    .replace(/\{%[\s\S]*?%\}/g, "/test/");
  dom.window.eval(script);
  dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));
  return { dom, options, show };
}

describe("calendar interactions", () => {
  it("opens event details and edit actions even when the end is missing", () => {
    const { dom, options, show } = setup();
    options.eventClick({ event: { title: "Meeting", start: new Date("2026-11-09T21:00:00Z"), end: null,
      allDay: false, extendedProps: { can_write: true, event_pk: 1 } } });
    expect(show).toHaveBeenCalledOnce();
    expect(dom.window.document.getElementById("eventDetailWhen").textContent).not.toContain("undefined");
    expect(dom.window.document.getElementById("eventDetailEditBtn").style.display).toBe("");
  });

  it("confirms link regeneration and cancels submission when declined", () => {
    const { dom } = setup();
    dom.window.confirm = vi.fn(() => false);
    const form = dom.window.document.getElementById("regenerateTokenForm");
    const event = new dom.window.Event("submit", { cancelable: true });
    expect(form.dispatchEvent(event)).toBe(false);
    expect(dom.window.confirm).toHaveBeenCalledOnce();
    dom.window.confirm.mockReturnValue(true);
    expect(form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }))).toBe(true);
  });

  it("does not request the nonexistent FullCalendar stylesheet or use an inline submit handler", () => {
    expect(template).not.toContain("index.global.min.css");
    expect(template).not.toContain('onsubmit="return confirm');
  });
});