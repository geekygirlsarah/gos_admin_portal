(function() {
    function initDualListbox() {
        const containers = document.querySelectorAll('.dual-listbox');
        
        containers.forEach(container => {
            if (container.dataset.initialized) return;
            container.dataset.initialized = 'true';

            const availableSelect = container.querySelector('.dual-listbox-available');
            const selectedSelect = container.querySelector('.dual-listbox-selected');
            const addButton = container.querySelector('.dual-listbox-add');
            const removeButton = container.querySelector('.dual-listbox-remove');
            const addAllButton = container.querySelector('.dual-listbox-add-all');
            const removeAllButton = container.querySelector('.dual-listbox-remove-all');
            const searchAvailable = container.querySelector('.dual-listbox-search-available');
            const searchSelected = container.querySelector('.dual-listbox-search-selected');
            const form = container.closest('form');

            const moveSelected = (from, to) => {
                const options = Array.from(from.selectedOptions);
                if (options.length === 0) return;
                
                options.forEach(option => {
                    option.selected = false;
                    to.appendChild(option);
                    // Reset display in case it was filtered out
                    option.style.display = '';
                });
                sortSelect(to);
                container.dispatchEvent(new CustomEvent('dual-listbox:change', { detail: { action: 'move' } }));
            };

            const moveAll = (from, to) => {
                const options = Array.from(from.options);
                if (options.length === 0) return;

                options.forEach(option => {
                    option.selected = false;
                    to.appendChild(option);
                    option.style.display = '';
                });
                sortSelect(to);
                container.dispatchEvent(new CustomEvent('dual-listbox:change', { detail: { action: 'moveAll' } }));
            };

            const sortSelect = (select) => {
                const options = Array.from(select.options);
                options.sort((a, b) => a.text.localeCompare(b.text));
                select.innerHTML = '';
                options.forEach(option => select.appendChild(option));
            };

            const handleSearch = (input, select) => {
                const term = input.value.toLowerCase();
                Array.from(select.options).forEach(option => {
                    const text = option.text.toLowerCase();
                    option.style.display = text.includes(term) ? '' : 'none';
                });
            };

            addButton.addEventListener('click', () => moveSelected(availableSelect, selectedSelect));
            removeButton.addEventListener('click', () => moveSelected(selectedSelect, availableSelect));
            addAllButton.addEventListener('click', () => moveAll(availableSelect, selectedSelect));
            removeAllButton.addEventListener('click', () => moveAll(selectedSelect, availableSelect));

            availableSelect.addEventListener('dblclick', () => moveSelected(availableSelect, selectedSelect));
            selectedSelect.addEventListener('dblclick', () => moveSelected(selectedSelect, availableSelect));

            if (searchAvailable) {
                searchAvailable.addEventListener('input', () => handleSearch(searchAvailable, availableSelect));
            }
            if (searchSelected) {
                searchSelected.addEventListener('input', () => handleSearch(searchSelected, selectedSelect));
            }

            if (form) {
                form.addEventListener('submit', () => {
                    Array.from(selectedSelect.options).forEach(option => {
                        option.selected = true;
                    });
                });
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDualListbox);
    } else {
        initDualListbox();
    }
})();
