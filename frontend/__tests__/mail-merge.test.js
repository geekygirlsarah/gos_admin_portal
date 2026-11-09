import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  applyColumnGroupVisibility,
  fillPastedColumn,
  initMailMerge,
  parsePastedColumn,
  readPastedColumn,
  serializeMergeGrid,
  validateColumnName,
} from "../../programs/static/js/mail-merge.js";

describe("mail merge grid data helpers", () => {
  it("splits pasted spreadsheet rows and uses the first pasted column", () => {
    expect(parsePastedColumn("4\tcompleted\r\n2\tpending\r\n")).toEqual([
      "4",
      "2",
    ]);
  });

  const clipboard = (data) => ({ getData: (type) => data[type] || "" });
  const excelHtml = `<html><body><table><!--StartFragment-->
    <tr><td class=xl65>4.5</td><td>x</td></tr>
    <tr><td>
      2</td></tr>
    <tr><td></td></tr><!--EndFragment--></table></body></html>`;

  it("falls back to Excel's HTML table when plain text has no row breaks", () => {
    expect(
      readPastedColumn(clipboard({ "text/plain": "4.5 2 ", "text/html": excelHtml })),
    ).toEqual(["4.5", "2", ""]);
  });

  it("prefers multi-row plain text and leaves single values to the browser", () => {
    expect(
      readPastedColumn(clipboard({ "text/plain": "a\r\nb\r\n", "text/html": excelHtml })),
    ).toEqual(["a", "b"]);
    expect(readPastedColumn(clipboard({ "text/plain": "single" }))).toEqual([]);
    expect(readPastedColumn(clipboard({ "text/plain": "one\r\n" }))).toEqual(["one"]);
  });

  it("fills the column from an Excel HTML paste in the grid", () => {
    document.body.innerHTML = `
      <div data-mail-merge data-max-columns="20" data-reserved-fields="[]">
        <form class="js-mail-merge-form">
          <input id="merge-data"><input id="preview-index" value="0">
          <table id="merge-grid">
            <thead><tr><th data-custom-column-header="hours"></th></tr></thead>
            <tbody>
              <tr data-student-id="1"><td><input class="js-merge-custom-value" data-custom-column="hours"></td></tr>
              <tr data-student-id="2"><td><input class="js-merge-custom-value" data-custom-column="hours"></td></tr>
            </tbody>
          </table>
        </form>
      </div>`;
    initMailMerge(document);
    const inputs = document.querySelectorAll(".js-merge-custom-value");
    const event = new Event("paste", { bubbles: true, cancelable: true });
    event.clipboardData = clipboard({ "text/plain": "4.5 2", "text/html": excelHtml });
    inputs[0].dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(Array.from(inputs, (input) => input.value)).toEqual(["4.5", "2"]);
  });

  it("fills pasted values down from the selected row without replacing other rows", () => {
    expect(fillPastedColumn(["old", "keep", "keep"], ["one", "two"], 1)).toEqual([
      "old",
      "one",
      "two",
    ]);
  });

  it("serializes only checked students with custom columns", () => {
    document.body.innerHTML = `
      <table id="grid">
        <thead><tr><th data-custom-column-header="volunteer_hours"></th></tr></thead>
        <tbody>
          <tr data-student-id="12">
            <td><input class="js-merge-selected" type="checkbox" checked></td>
            <td><input class="js-merge-custom-value" data-custom-column="volunteer_hours" value="4.5"></td>
          </tr>
          <tr data-student-id="13">
            <td><input class="js-merge-selected" type="checkbox"></td>
            <td><input class="js-merge-custom-value" data-custom-column="volunteer_hours" value="2"></td>
          </tr>
        </tbody>
      </table>`;

    expect(serializeMergeGrid(document.querySelector("#grid"))).toEqual({
      columns: ["volunteer_hours"],
      rows: [{ student_id: 12, volunteer_hours: "4.5" }],
    });
  });

  it("clears old subgroup filters when the program changes", () => {
    document.body.innerHTML = `
      <div id="root" data-max-columns="20" data-reserved-fields="[]">
        <form class="js-mail-merge-form">
          <select id="merge-program"><option value="1">One</option><option value="2">Two</option></select>
          <select id="merge-teams" multiple><option value="3" selected>Old team</option></select>
          <select id="merge-crews" multiple><option value="4" selected>Old crew</option></select>
          <select id="merge-subteams" multiple><option value="5" selected>Old subteam</option></select>
          <input id="merge-data"><input id="preview-index">
          <table id="merge-grid"><thead><tr></tr></thead><tbody></tbody></table>
        </form>
      </div>`;
    const root = document.querySelector("#root");
    initMailMerge(root);
    root.querySelector("#merge-program").dispatchEvent(new Event("change"));

    expect(root.querySelector("#merge-teams").selectedOptions).toHaveLength(0);
    expect(root.querySelector("#merge-crews").selectedOptions).toHaveLength(0);
    expect(root.querySelector("#merge-subteams").selectedOptions).toHaveLength(0);
  });

  it("wires the page controls when initialized from the document", () => {
    document.body.innerHTML = `
      <div data-mail-merge data-max-columns="20" data-reserved-fields='["first_name"]'>
        <form class="js-mail-merge-form">
          <input id="merge-data"><input id="preview-index" value="0">
          <table id="merge-grid">
            <thead><tr><th><input type="checkbox" id="select-all-students" checked></th></tr></thead>
            <tbody>
              <tr data-student-id="12"><td><input class="js-merge-selected" type="checkbox" checked></td></tr>
              <tr data-student-id="13"><td><input class="js-merge-selected" type="checkbox" checked></td></tr>
            </tbody>
          </table>
          <button type="button" id="add-merge-column">Add</button>
          <span id="merge-column-count"></span>
        </form>
      </div>`;

    expect(() => initMailMerge(document)).not.toThrow();

    const selectAll = document.querySelector("#select-all-students");
    selectAll.checked = false;
    selectAll.dispatchEvent(new Event("change"));

    const checkboxes = Array.from(document.querySelectorAll(".js-merge-selected"));
    expect(checkboxes.every((checkbox) => !checkbox.checked)).toBe(true);
    expect(document.querySelector("#merge-column-count").textContent).toBe(
      "0 of 20 custom columns",
    );
  });

  it("updates the review label with selected students and recipient mode", () => {
    document.body.innerHTML = `
      <div data-mail-merge data-max-columns="20" data-reserved-fields="[]">
        <form class="js-mail-merge-form">
          <input id="merge-data"><input id="preview-index" value="0">
          <table id="merge-grid">
            <thead><tr><th><input type="checkbox" id="select-all-students" checked></th></tr></thead>
            <tbody>
              <tr data-student-id="12"><td><input class="js-merge-selected" type="checkbox" checked></td></tr>
              <tr data-student-id="13"><td><input class="js-merge-selected" type="checkbox" checked></td></tr>
              <tr data-student-id="14"><td><input class="js-merge-selected" type="checkbox" checked></td></tr>
            </tbody>
          </table>
          <input type="radio" name="recipients" value="students" checked>
          <input type="radio" name="recipients" value="students_and_parents">
          <button type="submit" id="merge-review-send">
            Review send to <span id="merge-review-count">3</span> student(s)<span id="merge-review-parents" class="d-none"> and parent(s)</span>
          </button>
        </form>
      </div>`;
    initMailMerge(document);

    const reviewButton = document.querySelector("#merge-review-send");
    const studentCount = document.querySelector("#merge-review-count");
    const parentSuffix = document.querySelector("#merge-review-parents");
    const selectedCheckboxes = document.querySelectorAll(".js-merge-selected");
    expect(studentCount.textContent).toBe("3");
    expect(parentSuffix.classList).toContain("d-none");

    selectedCheckboxes[0].checked = false;
    selectedCheckboxes[0].dispatchEvent(new Event("change", { bubbles: true }));
    expect(studentCount.textContent).toBe("2");

    const parentsRadio = document.querySelector(
      'input[name="recipients"][value="students_and_parents"]',
    );
    parentsRadio.checked = true;
    parentsRadio.dispatchEvent(new Event("change", { bubbles: true }));
    expect(parentSuffix.classList).not.toContain("d-none");
    expect(reviewButton.textContent.trim()).toBe(
      "Review send to 2 student(s) and parent(s)",
    );

    document.querySelector("#select-all-students").checked = false;
    document.querySelector("#select-all-students").dispatchEvent(new Event("change"));
    expect(studentCount.textContent).toBe("0");
    expect(reviewButton.disabled).toBe(true);
  });
});

describe("mail merge custom column names", () => {
  const originalBootstrap = window.bootstrap;

  afterEach(() => {
    if (originalBootstrap === undefined) delete window.bootstrap;
    else window.bootstrap = originalBootstrap;
  });

  it("rejects malformed, duplicate, reserved, and over-limit names", () => {
    expect(validateColumnName("bad-name")).not.toBe("");
    expect(validateColumnName("note", ["note"])).toContain(
      "already have a custom column",
    );
    expect(validateColumnName("first_name", [], new Set(["first_name"]))).toContain(
      "built-in column",
    );
    expect(validateColumnName("note", ["one", "two"], new Set(), 2)).toContain("limit");
    expect(validateColumnName("volunteer_hours")).toBe("");
  });

  it("keeps the modal open for invalid names and adds a valid column", () => {
    let hideCalls = 0;
    window.bootstrap = {
      Modal: {
        getOrCreateInstance: () => ({ hide: () => hideCalls++ }),
      },
    };
    document.body.innerHTML = `
      <div data-mail-merge data-max-columns="2" data-reserved-fields='["first_name"]'>
        <form class="js-mail-merge-form">
          <input id="merge-data"><input id="preview-index" value="0">
          <table id="merge-grid">
            <thead><tr></tr></thead>
            <tbody><tr data-student-id="12"><td><input class="js-merge-selected" type="checkbox" checked></td></tr></tbody>
          </table>
        </form>
        <div id="merge-column-modal">
          <input id="merge-column-name" aria-describedby="merge-column-name-error">
          <div id="merge-column-name-error" class="invalid-feedback"></div>
          <button type="button" id="save-merge-column">Add column</button>
        </div>
        <button type="button" id="add-merge-column"></button>
        <span id="merge-column-count"></span>
      </div>`;
    initMailMerge(document);

    const modal = document.querySelector("#merge-column-modal");
    const input = document.querySelector("#merge-column-name");
    const saveButton = document.querySelector("#save-merge-column");
    const table = document.querySelector("#merge-grid");
    modal.dispatchEvent(new Event("show.bs.modal"));
    input.value = "bad-name";
    input.dispatchEvent(new Event("input"));
    saveButton.click();

    expect(input.classList).toContain("is-invalid");
    expect(document.querySelector("#merge-column-name-error").textContent).toContain(
      "lowercase",
    );
    expect(table.querySelectorAll("thead [data-custom-column-header]")).toHaveLength(0);
    expect(hideCalls).toBe(0);

    modal.dispatchEvent(new Event("show.bs.modal"));
    expect(input.value).toBe("");
    expect(document.querySelector("#merge-column-name-error").textContent).toBe("");
    input.value = "volunteer_hours";
    input.dispatchEvent(new Event("input"));
    expect(input.classList).toContain("is-valid");
    const enterEvent = new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    });
    input.dispatchEvent(enterEvent);

    expect(enterEvent.defaultPrevented).toBe(true);
    expect(hideCalls).toBe(1);
    expect(table.querySelector('thead [data-custom-column-header="volunteer_hours"]')).not.toBeNull();
    expect(
      table.querySelector('tbody [data-custom-column="volunteer_hours"]'),
    ).not.toBeNull();
    expect(document.querySelector("#merge-column-count").textContent).toBe(
      "1 of 2 custom columns",
    );
  });
});

describe("mail merge column visibility", () => {
  const gridHtml = `
    <div data-mail-merge data-max-columns="20" data-reserved-fields="[]">
      <input type="checkbox" class="js-merge-column-toggle" data-column-group="attendance" checked>
      <input type="checkbox" class="js-merge-column-toggle" data-column-group="outreach" checked>
      <form class="js-mail-merge-form">
        <input id="merge-data"><input id="preview-index" value="0">
        <table id="merge-grid">
          <thead><tr>
            <th class="merge-sticky-col"></th>
            <th class="merge-sticky-col">First name</th>
            <th data-column-group="attendance">Hours</th>
            <th data-column-group="outreach">Shifts</th>
            <th data-custom-column-header="note">note</th>
          </tr></thead>
          <tbody><tr data-student-id="12">
            <td class="merge-sticky-col"><input class="js-merge-selected" type="checkbox" checked></td>
            <td class="merge-sticky-col">Ada</td>
            <td data-column-group="attendance">4.0</td>
            <td data-column-group="outreach">2</td>
            <td><input class="js-merge-custom-value" data-custom-column="note" value="hi"></td>
          </tr></tbody>
        </table>
      </form>
    </div>`;

  beforeEach(() => {
    window.localStorage.clear();
  });

  it("hides only cells in the hidden groups", () => {
    document.body.innerHTML = gridHtml;
    const table = document.querySelector("#merge-grid");

    applyColumnGroupVisibility(table, ["attendance"]);

    const hidden = Array.from(table.querySelectorAll(".merge-column-hidden"));
    expect(hidden).toHaveLength(2);
    expect(hidden.every((cell) => cell.dataset.columnGroup === "attendance")).toBe(true);
  });

  it("toggles groups, keeps sticky and custom columns, and remembers the choice", () => {
    document.body.innerHTML = gridHtml;
    initMailMerge(document);

    const outreachToggle = document.querySelector('[data-column-group="outreach"].js-merge-column-toggle');
    outreachToggle.checked = false;
    outreachToggle.dispatchEvent(new Event("change"));

    const table = document.querySelector("#merge-grid");
    expect(table.querySelectorAll('td[data-column-group="outreach"].merge-column-hidden')).toHaveLength(1);
    expect(table.querySelector('td[data-column-group="attendance"]').classList).not.toContain(
      "merge-column-hidden",
    );
    expect(table.querySelectorAll(".merge-sticky-col.merge-column-hidden")).toHaveLength(0);
    expect(table.querySelector(".js-merge-custom-value").closest("td").classList).not.toContain(
      "merge-column-hidden",
    );
    const stickyCells = table.querySelectorAll("tbody .merge-sticky-col");
    expect(stickyCells[0].style.left).toBe("0px");
    expect(stickyCells[1].classList).toContain("merge-sticky-last");

    // A reloaded page restores the hidden group.
    document.body.innerHTML = gridHtml;
    initMailMerge(document);
    expect(
      document.querySelector('[data-column-group="outreach"].js-merge-column-toggle').checked,
    ).toBe(false);
    expect(
      document.querySelector('td[data-column-group="outreach"]').classList,
    ).toContain("merge-column-hidden");
  });
});
