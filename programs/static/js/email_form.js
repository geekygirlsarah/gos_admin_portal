(function() {
    function init() {
        var hiddenBody = document.getElementById('id_body');
        var editorEl = document.getElementById('editor');
        var quill = null;

        if (!window.Quill) {
            console.error('Quill library failed to load. Falling back to plain textarea.');
            if (editorEl) {
                var ta = document.createElement('textarea');
                ta.className = 'form-control';
                ta.rows = 12;
                ta.value = hiddenBody && hiddenBody.value ? hiddenBody.value : '';
                editorEl.replaceWith(ta);
                var form = ta.form;
                if (form) {
                    form.addEventListener('submit', function() {
                        if (hiddenBody) hiddenBody.value = ta.value;
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
            modulesConfig.keyboard = { bindings: quillBetterTable.keyboardBindings };
        }

        try {
            quill = new Quill('#editor', { theme: 'snow', modules: modulesConfig });
        } catch (e) {
            console.error('Failed to initialize Quill. Falling back to plain textarea.', e);
            var ta2 = document.createElement('textarea');
            ta2.className = 'form-control';
            ta2.rows = 12;
            ta2.value = hiddenBody && hiddenBody.value ? hiddenBody.value : '';
            editorEl.replaceWith(ta2);
            var form2 = ta2.form;
            if (form2) {
                form2.addEventListener('submit', function() {
                    if (hiddenBody) hiddenBody.value = ta2.value;
                }, { capture: true });
            }
            return;
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

        // Initialize from hidden input
        if (hiddenBody && hiddenBody.value) {
            quill.root.innerHTML = hiddenBody.value;
        }

        // Keep hidden body synchronized on content changes
        function syncBody() {
            var isTrulyEmpty = quill.getText().trim().length === 0;
            var html = isTrulyEmpty ? '' : quill.root.innerHTML;
            if (html && /<table[\s>]/i.test(html)) {
                // Ensure basic borders/padding for tables in email clients.
                var style = '<style>.email-table table{border-collapse:collapse;width:100%}.email-table td,.email-table th{border:1px solid #dee2e6;padding:6px}</style>';
                // Wrap content so premailer can inline these styles reliably
                html = style + '<div class="email-table">' + html + '</div>';
            }
            hiddenBody.value = html;
        }
        quill.on('text-change', syncBody);

        // Sync on submit
        var form = hiddenBody.form;
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