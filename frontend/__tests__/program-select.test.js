import { afterEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const programSelectScript = readFileSync(
  resolve("programs", "static", "js", "program_select.js"),
  "utf8",
);

const programSelectHtml = (selectedProgram = "current") => `
  <div class="col-lg-3">
    <select id="merge-program" name="program">
      <optgroup label="Active Programs">
        <option value="current" ${selectedProgram === "current" ? "selected" : ""}>Current Program (2026)</option>
      </optgroup>
      <optgroup label="Past / Archived Programs">
        <option value="past" ${selectedProgram === "past" ? "selected" : ""}>Past Program (2024-2025)</option>
      </optgroup>
    </select>
    <input class="js-toggle-past-programs" type="checkbox" data-program-select="merge-program">
  </div>
`;

const initializeProgramSelect = () => {
  new Function(programSelectScript)();
  document.dispatchEvent(new Event("DOMContentLoaded"));
};

describe("shared program selector", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("hides past programs until the switch is enabled", () => {
    document.body.innerHTML = programSelectHtml();

    initializeProgramSelect();

    const select = document.querySelector("#merge-program");
    const toggle = document.querySelector(".js-toggle-past-programs");
    expect(toggle.checked).toBe(false);
    expect(select.querySelector('optgroup[label="Past / Archived Programs"]')).toBeNull();

    toggle.checked = true;
    toggle.dispatchEvent(new Event("change"));

    expect(select.querySelector('optgroup[label="Past / Archived Programs"]')).not.toBeNull();
  });

  it("shows and checks the switch when a past program is selected", () => {
    document.body.innerHTML = programSelectHtml("past");

    initializeProgramSelect();

    const select = document.querySelector("#merge-program");
    const toggle = document.querySelector(".js-toggle-past-programs");
    expect(toggle.checked).toBe(true);
    expect(select.querySelector('optgroup[label="Past / Archived Programs"]')).not.toBeNull();
  });
});