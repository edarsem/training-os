const API_BASE = 'http://localhost:8000/api';

document.addEventListener('alpine:init', () => {
    Alpine.data('chatHistoryApp', () => ({
        conversations: [],
        isLoading: false,
        error: '',

        async init() {
            await this.fetchConversations();
        },

        async fetchConversations() {
            this.error = '';
            this.isLoading = true;
            try {
                const res = await fetch(`${API_BASE}/chat/conversations`);
                const data = await res.json();
                if (!res.ok) {
                    throw new Error(data?.detail || 'Failed to load conversations');
                }
                this.conversations = data || [];
            } catch (e) {
                this.error = e?.message || 'Failed to load conversations';
            } finally {
                this.isLoading = false;
            }
        },

        async createAndOpenConversation() {
            window.location.href = 'index.html';
        },

        async deleteConversation(conversationId) {
            try {
                const res = await fetch(`${API_BASE}/chat/conversations/${conversationId}`, {
                    method: 'DELETE'
                });
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    throw new Error(data?.detail || 'Failed to delete conversation');
                }
                this.conversations = this.conversations.filter((item) => item.id !== conversationId);
            } catch (e) {
                this.error = e?.message || 'Failed to delete conversation';
            }
        },

        formatDate(value) {
            if (!value) return 'Unknown date';
            const d = new Date(value);
            if (Number.isNaN(d.getTime())) return value;
            return new Intl.DateTimeFormat(navigator.language || 'en-US', {
                dateStyle: 'medium',
                timeStyle: 'short'
            }).format(d);
        }
    }));
});
