const API_BASE = 'http://localhost:8000/api';

function memoryApp() {
    return {
        items: [],
        formKey: '',
        formValue: '',
        editingKey: null,
        error: '',

        async init() {
            await this.loadItems();
        },

        async loadItems() {
            const res = await fetch(`${API_BASE}/memory`);
            this.items = await res.json();
        },

        startEdit(item) {
            this.editingKey = item.key;
            this.formKey = item.key;
            this.formValue = item.value;
            this.error = '';
        },

        cancelEdit() {
            this.editingKey = null;
            this.formKey = '';
            this.formValue = '';
            this.error = '';
        },

        async saveItem() {
            const key = this.formKey.trim();
            const value = this.formValue.trim();
            if (!key || !value) {
                this.error = 'Both key and value are required.';
                return;
            }
            this.error = '';
            await fetch(`${API_BASE}/memory`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key, value, source: 'user' }),
            });
            this.formKey = '';
            this.formValue = '';
            this.editingKey = null;
            await this.loadItems();
        },

        async deleteItem(id) {
            if (!confirm('Delete this memory item?')) return;
            await fetch(`${API_BASE}/memory/${id}`, { method: 'DELETE' });
            await this.loadItems();
        },
    };
}
