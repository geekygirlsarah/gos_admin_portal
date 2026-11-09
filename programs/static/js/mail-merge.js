export function parsePastedColumn(text) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines.map((line) => line.split("\t")[0]);
}

export function parsePastedHtmlColumn(html) {
  if (!html || !/<tr[\s>]/i.test(html) || typeof DOMParser === "undefined") return [];
  const doc = new DOMParser().parseFromString(html, "text/html");
  return Array.from(doc.querySelectorAll("tr"), (row) => {
    const cell = row.querySelector("td, th");
    return (cell?.textContent || "").replace(/\s+/g, " ").trim();
  });
}

// Firefox on Windows can hand Excel data to single-line inputs as plain text
// without row breaks, so fall back to the HTML table Excel also provides.
export function readPastedColumn(clipboardData) {
  if (!clipboardData) return [];
  const text = clipboardData.getData("text/plain") || clipboardData.getData("text") || "";
  if (/[\r\n]/.test(text.replace(/[\r\n]+$/, ""))) return parsePastedColumn(text);
  const htmlValues = parsePastedHtmlColumn(clipboardData.getData("text/html"));
  if (htmlValues.length > 1) return htmlValues;
  if (/[\r\n\t]/.test(text)) return parsePastedColumn(text);
  return [];
}

export function fillPastedColumn(currentValues, pastedValues, startIndex = 0) {
  const result = [...currentValues];
  pastedValues.forEach((value, index) => {
    const rowIndex = startIndex + index;
    if (rowIndex < result.length) result[rowIndex] = value;
  });
  return result;
}

export function serializeMergeGrid(table) {
  if (!table) return { columns: [], rows: [] };
  const columns = Array.from(
    table.querySelectorAll("thead [data-custom-column-header]"),
    (header) => header.dataset.customColumnHeader,
  );
  const rows = Array.from(table.querySelectorAll("tbody tr"))
    .filter((row) => row.querySelector(".js-merge-selected")?.checked)
    .map((row) => {
      const serialized = { student_id: Number(row.dataset.studentId) };
      row.querySelectorAll(".js-merge-custom-value").forEach((input) => {
        serialized[input.dataset.customColumn] = input.value;
      });
      return serialized;
    });
  return { columns, rows };
}

export function validateColumnName(name, existing = [], reserved = new Set(), maxColumns = 20) {
  if (!/^[a-z][a-z0-9_]{0,49}$/.test(name)) {
    return "Use only lowercase letters, numbers and underscores (_), starting with a letter. For example: due_date or note_1.";
  }
  if (reserved.has(name)) {
    return "This name is already used by a built-in column. Choose a different name.";
  }
  if (existing.includes(name)) return "You already have a custom column with this name.";
  if (existing.length >= maxColumns) {
    return `You have reached the limit of ${maxColumns} custom columns.`;
  }
  return "";
}

const HIDDEN_GROUPS_STORAGE_KEY = "momentum.mailMerge.hiddenColumnGroups";

export function applyColumnGroupVisibility(table, hiddenGroups) {
  if (!table) return;
  const hidden = new Set(hiddenGroups);
  table.querySelectorAll("[data-column-group]").forEach((cell) => {
    cell.classList.toggle("merge-column-hidden", hidden.has(cell.dataset.columnGroup));
  });
}

export function layoutStickyColumns(table) {
  if (!table) return;
  const headers = Array.from(table.querySelectorAll("thead .merge-sticky-col"));
  const offsets = [];
  let left = 0;
  headers.forEach((header) => {
    offsets.push(left);
    left += header.getBoundingClientRect().width;
  });
  table.querySelectorAll("tr").forEach((row) => {
    const cells = Array.from(row.children).filter((cell) =>
      cell.classList.contains("merge-sticky-col"),
    );
    cells.forEach((cell, index) => {
      cell.style.left = `${offsets[index] ?? 0}px`;
      cell.classList.toggle("merge-sticky-last", index === cells.length - 1);
    });
  });
}

function readHiddenGroups() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(HIDDEN_GROUPS_STORAGE_KEY) || "[]");
    return Array.isArray(stored) ? stored.filter((value) => typeof value === "string") : [];
  } catch {
    return [];
  }
}

function storeHiddenGroups(groups) {
  try {
    window.localStorage.setItem(HIDDEN_GROUPS_STORAGE_KEY, JSON.stringify(groups));
  } catch {
    // Storage can be unavailable (private browsing); visibility still works for this page.
  }
}

function insertIntoTextarea(textarea, value) {
  const start = textarea.selectionStart ?? textarea.value.length;
  const end = textarea.selectionEnd ?? start;
  textarea.setRangeText(value, start, end, "end");
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

function addCustomColumn(table, columnName) {
  const headerRow = table.querySelector("thead tr");
  const header = document.createElement("th");
  header.scope = "col";
  header.dataset.customColumnHeader = columnName;
  header.textContent = columnName;
  headerRow.appendChild(header);

  table.querySelectorAll("tbody tr").forEach((row) => {
    const cell = document.createElement("td");
    const input = document.createElement("input");
    input.className = "form-control form-control-sm js-merge-custom-value";
    input.type = "text";
    input.dataset.customColumn = columnName;
    input.maxLength = 1000;
    input.autocomplete = "off";
    cell.appendChild(input);
    row.appendChild(cell);
  });
}

export function initMailMerge(root = document) {
  const container =
    (root.matches?.("[data-mail-merge]") && root) ||
    root.querySelector("[data-mail-merge]") ||
    root;
  const settings = container.dataset || {};
  const programSelect = root.querySelector("#merge-program");

  if (programSelect) {
    programSelect.addEventListener("change", () => {
      root.querySelectorAll("#merge-teams, #merge-crews, #merge-subteams").forEach((select) => {
        Array.from(select.options).forEach((option) => {
          option.selected = false;
        });
      });
    });
  }

  const form = root.querySelector(".js-mail-merge-form");
  const table = root.querySelector("#merge-grid");
  if (!form || !table) return;

  const maxColumns = Number(settings.maxColumns || 20);
  const reservedFields = new Set(JSON.parse(settings.reservedFields || "[]"));
  const addColumnButton = root.querySelector("#add-merge-column");
  const columnCount = root.querySelector("#merge-column-count");
  const mergeData = root.querySelector("#merge-data");
  const previewIndex = root.querySelector("#preview-index");
  const columnModalElement = root.querySelector("#merge-column-modal");
  const columnNameInput = root.querySelector("#merge-column-name");
  const columnNameError = root.querySelector("#merge-column-name-error");
  const saveColumnButton = root.querySelector("#save-merge-column");
  const columnModal = columnModalElement
    ? window.bootstrap?.Modal?.getOrCreateInstance(columnModalElement)
    : null;

  const existingColumnNames = () =>
    Array.from(
      table.querySelectorAll("thead [data-custom-column-header]"),
      (header) => header.dataset.customColumnHeader,
    );

  const updateColumnCount = () => {
    const count = table.querySelectorAll("thead [data-custom-column-header]").length;
    if (columnCount) columnCount.textContent = `${count} of ${maxColumns} custom columns`;
    if (addColumnButton) addColumnButton.disabled = count >= maxColumns;
  };

  const validateColumnInput = () => {
    if (!columnNameInput) return "";
    const name = columnNameInput.value;
    const error = validateColumnName(name, existingColumnNames(), reservedFields, maxColumns);
    columnNameInput.classList.toggle("is-invalid", Boolean(error));
    columnNameInput.classList.toggle("is-valid", Boolean(name) && !error);
    columnNameInput.setAttribute("aria-invalid", String(Boolean(error)));
    if (columnNameError) columnNameError.textContent = error;
    return error;
  };

  const resetColumnInput = () => {
    if (!columnNameInput) return;
    columnNameInput.value = "";
    columnNameInput.classList.remove("is-invalid", "is-valid");
    columnNameInput.setAttribute("aria-invalid", "false");
    if (columnNameError) columnNameError.textContent = "";
  };

  if (columnModalElement && columnNameInput) {
    columnModalElement.addEventListener("show.bs.modal", resetColumnInput);
    columnNameInput.addEventListener("input", validateColumnInput);
    columnNameInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        saveColumnButton?.click();
      }
    });
  }

  if (saveColumnButton && columnNameInput) {
    saveColumnButton.addEventListener("click", () => {
      if (validateColumnInput()) {
        columnNameInput.focus();
        return;
      }
      addCustomColumn(table, columnNameInput.value);
      updateColumnCount();
      columnModal?.hide();
      table
        .querySelector(
          `tbody tr .js-merge-custom-value[data-custom-column="${columnNameInput.value}"]`,
        )
        ?.focus();
    });
  }

  table.addEventListener("paste", (event) => {
    const input = event.target.closest(".js-merge-custom-value");
    if (!input) return;
    const values = readPastedColumn(event.clipboardData);
    if (!values.length) return;
    event.preventDefault();
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    const startIndex = rows.indexOf(input.closest("tr"));
    const columnInputs = rows.map((row) =>
      Array.from(row.querySelectorAll(".js-merge-custom-value")).find(
        (cell) => cell.dataset.customColumn === input.dataset.customColumn,
      ),
    );
    const currentValues = columnInputs.map((cell) => cell?.value || "");
    fillPastedColumn(currentValues, values, startIndex).forEach((value, index) => {
      if (index >= startIndex && index < startIndex + values.length && columnInputs[index]) {
        columnInputs[index].value = value.slice(0, 1000);
      }
    });
  });

  const columnToggles = Array.from(root.querySelectorAll(".js-merge-column-toggle"));
  const syncColumnVisibility = () => {
    const hiddenGroups = columnToggles
      .filter((toggle) => !toggle.checked)
      .map((toggle) => toggle.dataset.columnGroup);
    applyColumnGroupVisibility(table, hiddenGroups);
    layoutStickyColumns(table);
    return hiddenGroups;
  };
  const storedHiddenGroups = new Set(readHiddenGroups());
  columnToggles.forEach((toggle) => {
    toggle.checked = !storedHiddenGroups.has(toggle.dataset.columnGroup);
    toggle.addEventListener("change", () => storeHiddenGroups(syncColumnVisibility()));
  });
  syncColumnVisibility();
  window.addEventListener("resize", () => layoutStickyColumns(table));

  const selectAll = root.querySelector("#select-all-students");
  const reviewButton = root.querySelector("#merge-review-send");
  const reviewCount = root.querySelector("#merge-review-count");
  const reviewParents = root.querySelector("#merge-review-parents");
  const updateReviewLabel = () => {
    const selectedCount = table.querySelectorAll(".js-merge-selected:checked").length;
    if (reviewCount) reviewCount.textContent = String(selectedCount);
    const includeParents =
      root.querySelector('input[name="recipients"]:checked')?.value ===
      "students_and_parents";
    reviewParents?.classList.toggle("d-none", !includeParents);
    if (reviewButton) reviewButton.disabled = selectedCount === 0;
  };

  table.addEventListener("change", (event) => {
    if (event.target.matches(".js-merge-selected")) updateReviewLabel();
  });
  root.querySelectorAll('input[name="recipients"]').forEach((radio) => {
    radio.addEventListener("change", updateReviewLabel);
  });

  if (selectAll) {
    selectAll.addEventListener("change", () => {
      table.querySelectorAll(".js-merge-selected").forEach((checkbox) => {
        checkbox.checked = selectAll.checked;
      });
      updateReviewLabel();
    });
  }

  root.querySelectorAll(".js-insert-merge-field").forEach((button) => {
    button.addEventListener("click", () => {
      const textarea = root.querySelector(`#${button.dataset.insertTarget}`);
      const token = `{{ ${button.dataset.insertField} }}`;
      if (!textarea) return;
      if (textarea.dataset.emailEditor !== undefined) {
        textarea.dispatchEvent(
          new CustomEvent("email-editor-insert", { detail: { text: token } }),
        );
      } else {
        insertIntoTextarea(textarea, token);
      }
    });
  });

  root.querySelectorAll(".js-preview-move").forEach((button) => {
    button.addEventListener("click", () => {
      const selectedCount = table.querySelectorAll(".js-merge-selected:checked").length;
      const current = Number(previewIndex?.value || 0);
      const delta = Number(button.dataset.previewMove || 0);
      if (previewIndex) {
        previewIndex.value = String(Math.max(0, Math.min(current + delta, selectedCount - 1)));
      }
    });
  });

  form.addEventListener("submit", () => {
    if (mergeData) mergeData.value = JSON.stringify(serializeMergeGrid(table));
  });
  updateColumnCount();
  updateReviewLabel();
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initMailMerge());
  } else {
    initMailMerge();
  }
}
