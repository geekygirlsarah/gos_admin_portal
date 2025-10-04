(function () {
  function init() {
    var editorEl = document.getElementById('editor');
    if (!editorEl) return;

    // Prefer the explicit body field, fall back to first matching hidden/textarea named body
    var hidden = document.getElementById('id_body') || document.querySelector('input[name="body"], textarea[name="body"]');

    // If Quill is unavailable, fall back to a plain textarea for a clean baseline
    if (!window.Quill) {
      var ta = document.createElement('textarea');
      ta.className = 'form-control';
      ta.rows = 12;
      if (hidden && typeof hidden.value === 'string') ta.value = hidden.value;
      editorEl.replaceWith(ta);
      var form = ta.form || (hidden && hidden.form) || (ta.closest ? ta.closest('form') : null);
      if (form) {
        form.addEventListener('submit', function () {
          if (hidden) hidden.value = ta.value;
        }, { capture: true });
      }
      return;
    }

    // Fresh, minimal Quill setup with the default toolbar
    var quill = new Quill('#editor', {
      theme: 'snow',
      modules: { toolbar: true }
    });

    // Load initial content from the hidden field if present
    try {
      var initial = hidden && typeof hidden.value === 'string' ? hidden.value : '';
      if (initial) {
        if (quill.clipboard && typeof quill.clipboard.dangerouslyPasteHTML === 'function') {
          quill.clipboard.dangerouslyPasteHTML(initial);
        } else {
          quill.root.innerHTML = initial;
        }
      }
    } catch (e) {}

    function syncHidden() {
      var isEmpty = quill.getText().trim().length === 0;
      var html = isEmpty ? '' : quill.root.innerHTML;
      if (hidden) hidden.value = html;
    }

    quill.on('text-change', syncHidden);

    // Ensure the latest content is saved on submit
    var form = (hidden && hidden.form) || (editorEl.closest ? editorEl.closest('form') : null);
    if (form) {
      form.addEventListener('submit', function () { try { syncHidden(); } catch (e) {} }, { capture: true });
    }

    window._quill = quill;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();