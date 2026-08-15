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

    // Register a soft line break blot. Quill 2.0's data model can only
    // represent paragraph breaks, so a single line break (Shift+Enter style)
    // otherwise becomes a whole new <p> block with paragraph spacing. This
    // blot renders a real <br>, so the email keeps tight single-line breaks.
    var softBreakBlot = null;
    try {
      var EmbedBlot = window.Quill.import('blots/embed');
      if (EmbedBlot) {
        // EmbedBlot is an ES6 class, so subclass it with `class extends`
        // rather than constructor-calling the parent (which throws).
        var SoftBreakBlot = class extends EmbedBlot {
          value() { return true; }
        };
        SoftBreakBlot.blotName = 'softbreak';
        SoftBreakBlot.tagName = 'BR';
        SoftBreakBlot.className = 'softbreak';
        window.Quill.register(SoftBreakBlot, true);
        softBreakBlot = SoftBreakBlot;
      }
    } catch (e) {}

    var quill = new Quill('#editor', { theme: 'snow', modules: modules });

    if (softBreakBlot) {
      // Convert pasted <br> elements into soft breaks instead of paragraphs.
      // A <br> is a soft break when it isn't the line-terminating <br> at the
      // end of a block (the only kind Quill renders for paragraph breaks).
      try {
        var Delta = window.Quill.import('delta');
        quill.clipboard.addMatcher('BR', function (node, delta) {
          var parent = node.parentNode;
          if (parent) {
            var children = parent.childNodes;
            var idx = Array.prototype.indexOf.call(children, node);
            for (var i = idx + 1; i < children.length; i++) {
              var c = children[i];
              var significant = c.nodeType === 1 || (c.nodeType === 3 && (c.nodeValue || '').trim() !== '');
              if (significant) {
                return new Delta().insert({ softbreak: true });
              }
            }
          }
          return delta;
        });
      } catch (e) {}
      // Shift+Enter inserts a soft break rather than a new paragraph. This
      // binding is unshifted ahead of Quill's default Enter handler.
      try {
        var enterBindings = quill.keyboard.bindings['Enter'];
        if (enterBindings && enterBindings.unshift) {
          enterBindings.unshift({
            key: 'Enter',
            shiftKey: true,
            handler: function (range) {
              quill.insertEmbed(range.index, 'softbreak', true, 'user');
              quill.insertText(range.index + 1, '\u200B', 'user');
              quill.setSelection(range.index + 1, 'silent');
              return false;
            }
          });
        }
      } catch (e) {}
    }

    // Convert pasted plain text to HTML, turning blank lines into paragraph
    // breaks and single newlines into <br> line breaks. Quill 2.0 only keeps
    // literal newlines when the clipboard carries no HTML at all; most apps
    // also put a text/html fragment on the clipboard (even for "plain"
    // pastes), and Quill's HTML normalizer collapses those newlines to spaces.
    function plainTextToHtml(text) {
      var normalized = text.replace(/\r\n/g, '\n').replace(/\n{3,}/g, '\n\n');
      var paras = normalized.split(/\n\n+/);
      var html = '';
      for (var i = 0; i < paras.length; i++) {
        if (paras[i]) {
          html += '<p>' + paras[i].replace(/\n/g, '<br>') + '</p>';
        }
      }
      return html;
    }

    // Google Docs and some other rich sources embed soft line breaks as
    // literal newlines inside their HTML (often &#10; within
    // white-space:pre-wrap spans). Quill collapses those newlines to a plain
    // space. Replace embedded newlines with <br> so they are preserved as
    // soft line breaks. Whitespace-only text nodes (e.g. pretty-printed HTML)
    // and code blocks (<pre>) are left alone.
    function convertEmbeddedNewlinesToBr(html) {
      try {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var queue = [doc.body];
        while (queue.length) {
          var node = queue.shift();
          var child = node.firstChild;
          while (child) {
            var next = child.nextSibling;
            if (child.nodeType === 3) {
              if (child.nodeValue.indexOf('\n') !== -1 && child.nodeValue.trim() !== '') {
                var segments = child.nodeValue.replace(/\r\n/g, '\n').split('\n');
                var frag = doc.createDocumentFragment();
                for (var i = 0; i < segments.length; i++) {
                  if (i > 0) frag.appendChild(doc.createElement('br'));
                  if (segments[i]) frag.appendChild(doc.createTextNode(segments[i]));
                }
                node.replaceChild(frag, child);
              }
            } else if (child.nodeType === 1) {
              var tag = child.tagName;
              if (tag !== 'PRE' && tag !== 'TEXTAREA' && tag !== 'SCRIPT' && tag !== 'STYLE') {
                queue.push(child);
              }
            }
            child = next;
          }
        }
        return doc.body.innerHTML;
      } catch (e) {
        return html;
      }
    }

    // Preserve line breaks when pasting text. Quill only keeps literal newlines
    // when the clipboard carries no HTML at all. Most apps also put a text/html
    // fragment on the clipboard (even for "plain" pastes), and Quill's HTML
    // normalizer collapses those newlines to spaces, so the email arrives as
    // one long line. Detect fragments whose line breaks are raw newlines rather
    // than real <p>/<br> structure, and rebuild them from the plain text with
    // explicit <p>/<br> tags. Truly rich HTML (paragraphs, lists, tables, etc.)
    // is left untouched.
    try {
      var clipboard = quill.clipboard;
      var origOnPaste = clipboard.onPaste;
      clipboard.onPaste = function (range, data) {
        if (data && typeof data.text === 'string' && data.text.indexOf('\n') !== -1) {
          var html = data.html || '';
          if (!html) {
            data.html = plainTextToHtml(data.text);
          } else {
            // Only skip a rebuild for genuinely rich content (paragraphs,
            // lists, tables, links, headings, images). Flat wrappers that
            // browsers use for plain-text copies (a single <span>/<div> with
            // literal newlines, or one <div> per line) are rebuilt from the
            // plain text so single newlines become <br> and blank lines
            // become paragraph breaks.
            var hasRichStructure = /<(p|li|ul|ol|table|tr|td|th|h[1-6]|blockquote|pre|img|a)\b/i.test(html);
            if (!hasRichStructure) {
              var stripped = html.replace(/<[^>]+>/g, '');
              var flatDivs = (html.match(/<div[\s>]/gi) || []).length;
              if (stripped.indexOf('\n') !== -1 || flatDivs > 1) {
                data.html = plainTextToHtml(data.text);
              }
            }
            // Rich HTML (e.g. Google Docs) may still embed soft line breaks as
            // newline characters inside its markup; those collapse to spaces
            // when Quill parses them, so turn them into <br> first.
            data.html = convertEmbeddedNewlinesToBr(data.html);
          }
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
