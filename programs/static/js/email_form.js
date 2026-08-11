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

    // Optional quill-better-table module (bundled for Quill 2.0)
    var qbt = window.quillBetterTable;
    if (qbt && qbt.default && typeof qbt.default === 'function') qbt = qbt.default;
    var hasTables = typeof qbt === 'function';
    if (hasTables) {
      try {
        window.Quill.register({ 'modules/better-table': qbt }, true);
      } catch (e) {
        hasTables = false;
      }
    }

    var toolbar = [
      [{ header: [1, 2, 3, false] }],
      ['bold', 'italic', 'underline', 'strike'],
      [{ list: 'ordered' }, { list: 'bullet' }],
      [{ align: [] }],
      ['link'],
      ['clean']
    ];
    var modules = { toolbar: { container: toolbar, handlers: {} } };
    if (hasTables) {
      toolbar.push(['table']);
      modules.toolbar.handlers.table = function () {
        this.quill.getModule('better-table').insertTable(3, 3);
      };
      modules['better-table'] = { operationMenu: {} };
      modules.keyboard = { bindings: window.quillBetterTable.keyboardBindings };
    }

    var quill = new Quill('#editor', { theme: 'snow', modules: modules });

    // Preserve line breaks when pasting plain text that contains literal \n
    // (e.g. copied from a terminal, code editor, or plain-text app). Quill's
    // default conversion turns those newlines into spaces. We only touch the
    // plain-text case (no tags), leaving rich HTML (GDocs etc.) untouched.
    try {
      var clipboard = quill.clipboard;
      var origOnPaste = clipboard.onPaste;
      clipboard.onPaste = function (range, data) {
        if (data && typeof data.html === 'string' && data.html.indexOf('<') === -1) {
          data.html = data.html.replace(/\r\n/g, '\n').replace(/\n/g, '<br>');
        }
        return origOnPaste.call(clipboard, range, data);
      };
    } catch (e) {}

    // Load initial content from the hidden field if present
    try {
      var initial = hidden && typeof hidden.value === 'string' ? hidden.value : '';
      if (initial) {
        quill.clipboard.dangerouslyPasteHTML(initial);
      }
    } catch (e) {}

    function syncHidden() {
      var isEmpty = quill.getText().trim().length === 0;
      var html = isEmpty ? '' : quill.root.innerHTML;
      if (html && /<table[\s>]/i.test(html)) {
        // Ensure basic borders/padding for tables in email clients.
        var style = '<style>.email-table table{border-collapse:collapse;width:100%}.email-table td,.email-table th{border:1px solid #dee2e6;padding:6px}</style>';
        // Wrap content so premailer can inline these styles reliably
        html = style + '<div class="email-table">' + html + '</div>';
      }
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
