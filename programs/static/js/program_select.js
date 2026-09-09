/**
 * Program Select with Past/Archived Programs Toggle
 *
 * Automatically enhances program select dropdowns containing a "Past / Archived Programs"
 * optgroup by adding/connecting a toggle switch ("Show past & archived programs").
 */
(function () {
  function initProgramSelects() {
    const selects = document.querySelectorAll("select");
    selects.forEach((selectEl) => {
      // Find an optgroup that represents past or archived programs
      const pastOptgroup = Array.from(selectEl.querySelectorAll("optgroup")).find(
        (og) => og.label && og.label.toLowerCase().includes("past")
      );
      if (!pastOptgroup) return;

      // Prevent double initialization
      if (selectEl.dataset.pastToggleInitialized === "true") return;
      selectEl.dataset.pastToggleInitialized = "true";

      // Find or create toggle switch container
      let toggleInput = null;
      const selectId = selectEl.id || "";
      if (selectId) {
        toggleInput = document.querySelector(
          `input[data-program-select="${selectId}"], #togglePastPrograms_${selectId}`
        );
      }
      if (!toggleInput) {
        const parentCol =
          selectEl.closest(".col, .col-md-3, .col-md-4, .col-sm-6, .mb-3, .mb-4") ||
          selectEl.parentElement;
        toggleInput = parentCol ? parentCol.querySelector(".js-toggle-past-programs") : null;
      }

      // If no toggle was rendered in HTML, dynamically create one right below the select
      if (!toggleInput) {
        const container = document.createElement("div");
        container.className = "form-check form-switch mt-1 js-past-programs-toggle-container";
        const uniqueId = "toggle_past_" + Math.random().toString(36).substring(2, 9);
        container.innerHTML = `
          <input class="form-check-input js-toggle-past-programs" type="checkbox" id="${uniqueId}">
          <label class="form-check-label small text-muted" for="${uniqueId}">
            <i class="bi bi-archive me-1"></i>Show past &amp; archived programs
          </label>
        `;
        selectEl.parentNode.insertBefore(container, selectEl.nextSibling);
        toggleInput = container.querySelector("input");
      }

      // Determine initial state: if the currently selected option is in pastOptgroup, show it
      const selectedOption = selectEl.options[selectEl.selectedIndex];
      const isPastSelected = selectedOption && pastOptgroup.contains(selectedOption);

      const placeholder = document.createComment("past-optgroup-placeholder");

      function setVisibility(show) {
        if (show) {
          if (placeholder.parentNode) {
            placeholder.parentNode.insertBefore(pastOptgroup, placeholder);
            placeholder.remove();
          }
          pastOptgroup.hidden = false;
          pastOptgroup.style.display = "";
        } else {
          const currSelected = selectEl.options[selectEl.selectedIndex];
          const currIsPast = currSelected && pastOptgroup.contains(currSelected);
          if (!currIsPast || selectEl.value === "") {
            if (pastOptgroup.parentNode) {
              pastOptgroup.parentNode.insertBefore(placeholder, pastOptgroup);
              pastOptgroup.remove();
            }
          } else {
            pastOptgroup.hidden = true;
            pastOptgroup.style.display = "none";
          }
        }
      }

      if (isPastSelected) {
        toggleInput.checked = true;
        setVisibility(true);
      } else {
        toggleInput.checked = false;
        setVisibility(false);
      }

      toggleInput.addEventListener("change", function () {
        setVisibility(this.checked);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initProgramSelects);
  } else {
    initProgramSelects();
  }
})();
