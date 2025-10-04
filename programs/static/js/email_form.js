(function() {
    function init() {
        // Find one or more hidden fields we should sync HTML into.
        // Support both the general email form (name="body") and the balances form (name="default_message").
        var hiddenFields = [];
        var byId = document.getElementById('id_body');
        if (byId && (byId.tagName === 'INPUT' || byId.tagName === 'TEXTAREA')) {
            hiddenFields.push(byId);
        }
        hiddenFields = hiddenFields.concat([].slice.call(document.querySelectorAll('input[type="hidden"][name="body"], input[type="hidden"][name="default_message"], textarea[name="body"], textarea[name="default_message"]')));
        // Deduplicate
        hiddenFields = hiddenFields.filter(function(el, idx, arr){ return arr.indexOf(el) === idx; });

        var primaryHidden = hiddenFields.length ? hiddenFields[0] : null;
        var editorEl = document.getElementById('editor');
        var quill = null;

                // Helpers to get/set hidden field values
                function getFirstHiddenValue() {
                    for (var i = 0; i < hiddenFields.length; i++) {
                        var f = hiddenFields[i];
                        if (f && typeof f.value === 'string' && f.value.length) {
                            return f.value;
                        }
                    }
                    return '';
                }
                function setAllHiddenValues(val) {
                    hiddenFields.forEach(function(f){ if (f) f.value = val; });
                }
                function findForm() {
                    if (primaryHidden && primaryHidden.form) return primaryHidden.form;
                    if (editorEl) {
                        // closest() may not exist on older browsers; fallback simple search
                        if (typeof editorEl.closest === 'function') {
                            var fm = editorEl.closest('form');
                            if (fm) return fm;
                        }
                        var p = editorEl.parentElement;
                        while (p) { if (p.tagName === 'FORM') return p; p = p.parentElement; }
                    }
                    return null;
                }

        if (!window.Quill) {
            console.error('Quill library failed to load. Falling back to plain textarea.');
            if (editorEl) {
                var ta = document.createElement('textarea');
                ta.className = 'form-control';
                ta.rows = 12;
                ta.value = getFirstHiddenValue();
                editorEl.replaceWith(ta);
                var form = ta.form;
                if (form) {
                    form.addEventListener('submit', function() {
                        setAllHiddenValues(ta.value);
                    }, { capture: true });
                }
            }
            return;
        }

        // Try to register better-table; if it fails, continue without it
        var betterTableAvailable = false;
        try {
            if (typeof quillBetterTable !== 'undefined') {
                Quill.register({ 'modules/better-table': quillBetterTable }, true);
                betterTableAvailable = true;
            } else {
                console.warn('quill-better-table not found; proceeding without table module.');
            }
        } catch (e) {
            console.warn('Failed to register quill-better-table; proceeding without it.', e);
            betterTableAvailable = false;
        }

        // Whitelist font/size (optional)
        try {
            var Size = Quill.import('formats/size');
            Size.whitelist = ['small', false, 'large', 'huge'];
            Quill.register(Size, true);
            var Font = Quill.import('formats/font');
            Font.whitelist = ['sans-serif', 'serif', 'monospace'];
            Quill.register(Font, true);
        } catch (e) {
            console.warn('Failed to register font/size whitelists.', e);
        }

        var toolbarOptions = [
            [{ 'font': ['sans-serif', 'serif', 'monospace'] }],
            [{ 'size': ['small', false, 'large', 'huge'] }],
            [{ 'header': [1, 2, 3, 4, 5, 6, false] }],
            ['bold', 'italic', 'underline', 'strike'],
            [{ 'color': [] }, { 'background': [] }],
            [{ 'script': 'sub' }, { 'script': 'super' }],
            [{ 'align': [] }],
            [{ 'list': 'ordered' }, { 'list': 'bullet' }],
            ['blockquote', 'code-block'],
            ['link', 'image'],
            ['clean']
        ];

        var modulesConfig = { toolbar: toolbarOptions };
        if (betterTableAvailable) {
            modulesConfig.table = false; // disable Quill built-in table
            modulesConfig['better-table'] = {
                operationMenu: { items: { unmergeCells: { text: 'Unmerge Cells' } } }
            };
            // Do not override default keyboard bindings; quill-better-table augments them internally.
            // Overriding here removes Backspace binding leading to plugin errors.
            // modulesConfig.keyboard = { bindings: quillBetterTable.keyboardBindings };
        }

        try {
            quill = new Quill('#editor', { theme: 'snow', modules: modulesConfig });
        } catch (e) {
            console.error('Failed to initialize Quill. Falling back to plain textarea.', e);
            var ta2 = document.createElement('textarea');
            ta2.className = 'form-control';
            ta2.rows = 12;
            ta2.value = getFirstHiddenValue();
            editorEl.replaceWith(ta2);
            var form2 = ta2.form;
            if (form2) {
                form2.addEventListener('submit', function() {
                    setAllHiddenValues(ta2.value);
                }, { capture: true });
            }
            return;
        }

        // Ensure toolbar interactions focus the editor so actions apply
        try {
            var maybeToolbar = editorEl ? editorEl.previousElementSibling : null;
            if (maybeToolbar && maybeToolbar.classList && maybeToolbar.classList.contains('ql-toolbar')) {
                // Focus on mousedown so Quill has focus before button handlers run
                maybeToolbar.addEventListener('mousedown', function() {
                    try { quill.focus(); } catch (e) {}
                });
                // Also on click as a fallback
                maybeToolbar.addEventListener('click', function() {
                    try { quill.focus(); } catch (e) {}
                });
            }
        } catch (e) {
            // no-op
        }

        // Insert a simple "Insert 3×3 Table" button if plugin is present
        if (betterTableAvailable) {
            try {
                var toolbarRow = document.createElement('div');
                toolbarRow.className = 'mb-2';
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'btn btn-sm btn-outline-secondary';
                btn.textContent = 'Insert 3×3 Table';
                btn.addEventListener('click', function() {
                    quill.getModule('better-table').insertTable(3, 3);
                });
                editorEl.parentNode.insertBefore(toolbarRow, editorEl);
                toolbarRow.appendChild(btn);
            } catch (e) {
                console.warn('Failed to add Insert Table button.', e);
            }
        }

        // Initialize from hidden field(s)
        (function(){
            var initial = getFirstHiddenValue();
            if (initial && quill && quill.clipboard && typeof quill.clipboard.dangerouslyPasteHTML === 'function') {
                try { quill.clipboard.dangerouslyPasteHTML(initial); }
                catch (e) { try { quill.root.innerHTML = initial; } catch (e2) {} }
            } else if (initial) {
                try { quill.root.innerHTML = initial; } catch (e3) {}
            }
        })();

        // Keep hidden body synchronized on content changes
        function syncBody() {
            var isTrulyEmpty = quill.getText().trim().length === 0;
            var html = isTrulyEmpty ? '' : quill.root.innerHTML;
            if (html && /<table[\s>]/i.test(html)) {
                // Wrap content to allow scoping if needed; avoid inline <style> tags due to CSP.
                html = '<div class="email-table">' + html + '</div>';
            }
            setAllHiddenValues(html);
        }
        quill.on('text-change', syncBody);

        // Sync on submit
        var form = findForm();
        if (form) {
            form.addEventListener('submit', function() {
                try { syncBody(); } catch (e) {}
            }, { capture: true });
        }

        window._quill = quill;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();