const API_BASE = 'http://localhost:8000/api';

function toIsoDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function addDays(date, days) {
    const next = new Date(date);
    next.setDate(next.getDate() + days);
    return next;
}

function addWeeks(date, weeks) {
    return addDays(date, weeks * 7);
}

function getStartOfIsoWeek(date) {
    const start = new Date(date);
    const day = start.getDay();
    const diff = day === 0 ? -6 : 1 - day;
    start.setDate(start.getDate() + diff);
    start.setHours(0, 0, 0, 0);
    return start;
}

function getIsoWeekYear(date) {
    const target = new Date(date);
    target.setDate(target.getDate() + 4 - (target.getDay() || 7));
    return target.getFullYear();
}

function getIsoWeek(date) {
    const target = new Date(date);
    target.setDate(target.getDate() + 4 - (target.getDay() || 7));
    const yearStart = new Date(target.getFullYear(), 0, 1);
    return Math.ceil((((target - yearStart) / 86400000) + 1) / 7);
}

function getDayShortName(date) {
    return new Intl.DateTimeFormat('en-US', { weekday: 'short' }).format(date);
}

function formatMonthDay(date) {
    return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(date);
}

function formatMonthDayYear(date) {
    return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
}

function escapeHtml(text) {
    return String(text || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

document.addEventListener('alpine:init', () => {
    Alpine.data('app', () => ({
        currentDate: new Date(),
        currentYear: 0,
        currentWeek: 0,
        weekLabel: '',
        weekDays: [],
        
        summary: {
            sessions: [],
            day_notes: [],
            total_duration_minutes: 0,
            total_distance_km: 0,
            total_elevation_gain_m: 0
        },

        weekStats: {
            runTrailKm: '0.0',
            elevationM: 0,
            swimKm: '0.0',
            bikeKm: '0.0',
            strengthTime: "0'",
            totalTime: "0'"
        },
        
        plan: {},
        editPlan: false,
        planForm: { description: '', target_distance_km: null, target_sessions: null },

        isSessionModalOpen: false,
        sessionForm: { id: null, date: '', start_time: null, time_str: '', type: 'run', duration_minutes: 60, distance_km: null, elevation_gain_m: null, perceived_intensity: null, notes: '', strength_focuses: [] },
        isRefreshing: false,

        isNoteModalOpen: false,
        noteForm: { date: '', note: '' },

        chatMessages: [],
        activeConversationId: null,
        chatInput: '',
        chatModelOptions: [
            'mistral-small-latest',
            'mistral-medium-latest',
            'mistral-large-latest'
        ],
        selectedChatModel: 'mistral-small-latest',
        isChatLoading: false,
        chatError: '',
        chatDebug: {
            lastQuery: '',
            lastPayload: null,
            lastModelInput: null,
            lastMcpTrace: null,
            lastAudit: null,
            lastRawResponse: null
        },

        async init() {
            this.initializeChatModelSelection();
            this.updateWeekInfo();
            await this.fetchData();
            await this.loadInitialConversation();
        },

        initializeChatModelSelection() {
            const stored = localStorage.getItem('training_os_chat_model');
            if (stored && this.chatModelOptions.includes(stored)) {
                this.selectedChatModel = stored;
            } else {
                this.selectedChatModel = this.chatModelOptions[0];
                localStorage.setItem('training_os_chat_model', this.selectedChatModel);
            }
        },

        setChatModel(modelName) {
            if (!this.chatModelOptions.includes(modelName)) return;
            this.selectedChatModel = modelName;
            localStorage.setItem('training_os_chat_model', modelName);
        },

        handleChatInputKeydown(event) {
            if (!event || event.isComposing) return;
            if (event.key !== 'Enter') return;

            if (event.shiftKey) {
                return;
            }

            event.preventDefault();
            if (!this.isChatLoading && (this.chatInput || '').trim()) {
                this.sendChatMessage();
            }
        },

        updateWeekInfo() {
            this.currentYear = getIsoWeekYear(this.currentDate);
            this.currentWeek = getIsoWeek(this.currentDate);
            
            const start = getStartOfIsoWeek(this.currentDate);
            const end = addDays(start, 6);
            this.weekLabel = `${formatMonthDay(start)} - ${formatMonthDayYear(end)}`;

            this.weekDays = Array.from({ length: 7 }).map((_, i) => {
                const d = addDays(start, i);
                return {
                    date: d,
                    dateStr: toIsoDate(d),
                    dayName: getDayShortName(d),
                    dayNumber: String(d.getDate())
                };
            });
        },

        prevWeek() {
            this.currentDate = addWeeks(this.currentDate, -1);
            this.updateWeekInfo();
            this.fetchData();
        },

        nextWeek() {
            this.currentDate = addWeeks(this.currentDate, 1);
            this.updateWeekInfo();
            this.fetchData();
        },

        async fetchData() {
            try {
                const res = await fetch(`${API_BASE}/summary/week/${this.currentYear}/${this.currentWeek}`);
                if (res.ok) {
                    const data = await res.json();
                    this.summary = data;
                    this.computeWeekStats();
                    this.plan = data.plan || {};
                    this.planForm = { 
                        description: this.plan.description || '', 
                        target_distance_km: this.plan.target_distance_km || null, 
                        target_sessions: this.plan.target_sessions || null 
                    };
                }
            } catch (e) {
                console.error("Failed to fetch data", e);
            }
        },

        getConversationIdFromUrl() {
            const params = new URLSearchParams(window.location.search);
            const value = params.get('conversation_id');
            if (!value) return null;
            const parsed = Number(value);
            return Number.isFinite(parsed) ? parsed : null;
        },

        setConversationIdInUrl(conversationId) {
            const url = new URL(window.location.href);
            if (conversationId) {
                url.searchParams.set('conversation_id', String(conversationId));
            } else {
                url.searchParams.delete('conversation_id');
            }
            window.history.replaceState({}, '', url.toString());
        },

        async loadInitialConversation() {
            const conversationId = this.getConversationIdFromUrl();
            if (!conversationId) {
                this.activeConversationId = null;
                this.chatMessages = [];
                return;
            }
            await this.loadConversationMessages(conversationId);
        },

        async loadConversationMessages(conversationId) {
            try {
                const res = await fetch(`${API_BASE}/chat/conversations/${conversationId}/messages`);
                if (!res.ok) {
                    this.activeConversationId = null;
                    this.setConversationIdInUrl(null);
                    this.chatMessages = [];
                    return;
                }
                const messages = await res.json();
                this.activeConversationId = conversationId;
                this.chatMessages = (messages || []).map((message) => ({
                    role: message.role,
                    content: message.content
                }));
            } catch (e) {
                console.error('Failed to load conversation messages', e);
                this.chatMessages = [];
                this.activeConversationId = null;
            }
        },

        async ensureConversation() {
            if (this.activeConversationId) return this.activeConversationId;
            const res = await fetch(`${API_BASE}/chat/conversations`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: 'New chat' })
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data?.detail || 'Failed to create conversation');
            }
            const data = await res.json();
            this.activeConversationId = data.id;
            this.setConversationIdInUrl(data.id);
            return data.id;
        },

        async persistChatMessage(role, content) {
            const conversationId = await this.ensureConversation();
            const res = await fetch(`${API_BASE}/chat/conversations/${conversationId}/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role, content })
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data?.detail || 'Failed to persist chat message');
            }
        },

        async startNewChat() {
            this.activeConversationId = null;
            this.chatMessages = [];
            this.chatError = '';
            this.chatDebug = {
                lastQuery: '',
                lastPayload: null,
                lastModelInput: null,
                lastMcpTrace: null,
                lastAudit: null,
                lastRawResponse: null
            };
            this.setConversationIdInUrl(null);
            this.chatInput = '';
        },

        getSessionsForDate(dateStr) {
            return this.summary.sessions
                .filter((session) => session.date === dateStr)
                .sort((left, right) => {
                    const leftHasNotes = left.notes && left.notes.trim() !== '';
                    const rightHasNotes = right.notes && right.notes.trim() !== '';
                    if (leftHasNotes !== rightHasNotes) {
                        return leftHasNotes ? -1 : 1;
                    }

                    const leftTime = left.start_time ? new Date(left.start_time).getTime() : Number.MAX_SAFE_INTEGER;
                    const rightTime = right.start_time ? new Date(right.start_time).getTime() : Number.MAX_SAFE_INTEGER;
                    return leftTime - rightTime;
                });
        },

        getDayNote(dateStr) {
            return this.summary.day_notes.find(n => n.date === dateStr);
        },

        getTypeColor(type) {
            const colors = {
                run: 'bg-red-500',
                trail: 'bg-green-600',
                swim: 'bg-blue-500',
                hike: 'bg-amber-600',
                bike: 'bg-orange-500',
                skate: 'bg-sky-300',
                strength: 'bg-purple-500',
                mobility: 'bg-teal-500',
                other: 'bg-gray-500'
            };
            return colors[type] || colors.other;
        },

        getTypeLabel(type) {
            const labels = {
                run: '🏃',
                trail: '🏃',
                swim: '🏊',
                hike: '🥾',
                bike: '🚴',
                skate: '⛸️',
                strength: '💪',
                mobility: '🧘',
                other: '✨'
            };
            if (type === 'generic') return labels.other;
            return labels[type] || labels.other;
        },

        formatDuration(minutes) {
            const wholeMinutes = Math.max(0, Math.round(minutes || 0));
            const hours = Math.floor(wholeMinutes / 60);
            const mins = wholeMinutes % 60;
            if (hours > 0) {
                return `${hours}h${String(mins).padStart(2, '0')}'`;
            }
            return `${mins}'`;
        },

        formatKm(value) {
            return (Math.round((value || 0) * 10) / 10).toFixed(1);
        },

        computeWeekStats() {
            const sessions = this.summary.sessions || [];
            const runTrailKm = sessions
                .filter((session) => ['run', 'trail'].includes(session.type))
                .reduce((sum, session) => sum + (session.distance_km || 0), 0);

            const elevationM = sessions
                .filter((session) => ['run', 'trail', 'hike'].includes(session.type))
                .reduce((sum, session) => sum + (session.elevation_gain_m || 0), 0);

            const swimKm = sessions
                .filter((session) => session.type === 'swim')
                .reduce((sum, session) => sum + (session.distance_km || 0), 0);

            const bikeKm = sessions
                .filter((session) => session.type === 'bike')
                .reduce((sum, session) => sum + (session.distance_km || 0), 0);

            const strengthMinutes = sessions
                .filter((session) => session.type === 'strength')
                .reduce((sum, session) => sum + (session.moving_duration_minutes || session.duration_minutes || 0), 0);

            const totalMinutes = sessions
                .reduce((sum, session) => sum + (session.moving_duration_minutes || session.duration_minutes || 0), 0);

            this.weekStats = {
                runTrailKm: this.formatKm(runTrailKm),
                elevationM: Math.round(elevationM),
                swimKm: this.formatKm(swimKm),
                bikeKm: this.formatKm(bikeKm),
                strengthTime: this.formatDuration(strengthMinutes),
                totalTime: this.formatDuration(totalMinutes)
            };
        },

        formatTime(dateString) {
            if (!dateString) return '';
            const d = new Date(dateString);
            return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
        },

        formatSessionCompactDuration(session) {
            const movingMinutes = session.moving_duration_minutes || session.duration_minutes;
            return this.formatDuration(movingMinutes);
        },

        formatSessionCompactDistance(session) {
            if (!session.distance_km || !['run', 'trail', 'bike', 'hike', 'swim', 'skate'].includes(session.type)) {
                return '';
            }
            return `${this.formatKm(session.distance_km)} km`;
        },

        formatPace(value) {
            const pace = Number(value);
            if (!Number.isFinite(pace) || pace <= 0) return '';
            const mins = Math.floor(pace);
            const secs = Math.round((pace - mins) * 60);
            return `${mins}:${String(secs).padStart(2, '0')} /km`;
        },

        formatHeartRate(avg, max) {
            if (!avg) return '';
            const avgHr = Math.round(avg);
            if (max) {
                const maxHr = Math.round(max);
                return `${avgHr}/${maxHr} bpm`;
            }
            return `${avgHr} bpm`;
        },

        parseStrengthMeta(notesText) {
            if (!notesText) return { focuses: [], cleanNotes: '' };
            const match = notesText.match(/^\[StrengthFocus:\s*(.*?)\]\n?/);
            if (!match) return { focuses: [], cleanNotes: notesText };
            const focuses = match[1]
                .split(',')
                .map((item) => item.trim())
                .filter((item) => item.length > 0);
            const cleanNotes = notesText.replace(/^\[StrengthFocus:\s*.*?\]\n?/, '');
            return { focuses, cleanNotes };
        },

        getSessionNoteDisplay(session) {
            if (!session || !session.notes) return '';
            if (session.type !== 'strength') return session.notes;

            const parsed = this.parseStrengthMeta(session.notes);
            const focusText = (parsed.focuses || []).join(', ').trim();
            const cleanNotes = (parsed.cleanNotes || '').trim();

            if (focusText && cleanNotes) return `${focusText}\n${cleanNotes}`;
            if (focusText) return focusText;
            return cleanNotes;
        },

        buildNotesWithStrengthMeta(notesText, focuses) {
            const clean = (notesText || '').trim();
            if (!focuses || focuses.length === 0) {
                return clean || null;
            }
            const prefix = `[StrengthFocus: ${focuses.join(', ')}]`;
            return clean ? `${prefix}\n${clean}` : prefix;
        },

        toggleStrengthFocus(focus) {
            const current = this.sessionForm.strength_focuses || [];
            if (current.includes(focus)) {
                this.sessionForm.strength_focuses = current.filter((item) => item !== focus);
            } else {
                this.sessionForm.strength_focuses = [...current, focus];
            }
        },

        // --- Plan Actions ---
        async savePlan() {
            const payload = {
                year: this.currentYear,
                week_number: this.currentWeek,
                description: this.planForm.description,
                target_distance_km: this.planForm.target_distance_km ? parseFloat(this.planForm.target_distance_km) : null,
                target_sessions: this.planForm.target_sessions ? parseInt(this.planForm.target_sessions) : null
            };
            
            try {
                const res = await fetch(`${API_BASE}/plans`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    this.editPlan = false;
                    this.fetchData();
                }
            } catch (e) {
                console.error("Failed to save plan", e);
            }
        },

        // --- Session Actions ---
        openSessionModal(dateStr) {
            this.sessionForm = { id: null, date: dateStr, start_time: null, time_str: '', type: 'run', duration_minutes: 60, distance_km: null, elevation_gain_m: null, perceived_intensity: null, notes: '', strength_focuses: [] };
            this.isSessionModalOpen = true;
        },

        editSession(session) {
            const parsed = this.parseStrengthMeta(session.notes);
            this.sessionForm = {
                ...session,
                time_str: session.start_time ? this.formatTime(session.start_time) : '',
                notes: parsed.cleanNotes,
                strength_focuses: parsed.focuses
            };
            this.isSessionModalOpen = true;
        },

        async saveSession() {
            const isEdit = !!this.sessionForm.id;
            const url = isEdit ? `${API_BASE}/sessions/${this.sessionForm.id}` : `${API_BASE}/sessions`;
            const method = isEdit ? 'PUT' : 'POST';
            
            // Clean up empty strings to null
            const payload = { ...this.sessionForm };
            if (payload.distance_km === "") payload.distance_km = null;
            if (payload.elevation_gain_m === "") payload.elevation_gain_m = null;
            if (payload.perceived_intensity === "") payload.perceived_intensity = null;
            
            if (!isEdit) {
                if (payload.time_str) {
                    payload.start_time = new Date(`${payload.date}T${payload.time_str}:00`).toISOString();
                } else {
                    payload.start_time = null;
                }
            }
            payload.notes = this.buildNotesWithStrengthMeta(payload.notes, payload.type === 'strength' ? payload.strength_focuses : []);
            delete payload.time_str;
            delete payload.strength_focuses;

            try {
                const res = await fetch(url, {
                    method,
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    this.isSessionModalOpen = false;
                    this.fetchData();
                }
            } catch (e) {
                console.error("Failed to save session", e);
            }
        },

        async deleteSession() {
            if (!confirm("Are you sure you want to delete this session?")) return;
            try {
                const res = await fetch(`${API_BASE}/sessions/${this.sessionForm.id}`, { method: 'DELETE' });
                if (res.ok) {
                    this.isSessionModalOpen = false;
                    this.fetchData();
                }
            } catch (e) {
                console.error("Failed to delete session", e);
            }
        },

        // --- Note Actions ---
        openNoteModal(dateStr) {
            const existing = this.getDayNote(dateStr);
            this.noteForm = { date: dateStr, note: existing ? existing.note : '' };
            this.isNoteModalOpen = true;
        },

        async saveNote() {
            try {
                const res = await fetch(`${API_BASE}/day-notes`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.noteForm)
                });
                if (res.ok) {
                    this.isNoteModalOpen = false;
                    this.fetchData();
                }
            } catch (e) {
                console.error("Failed to save note", e);
            }
        },

        async refreshFromStrava() {
            if (this.isRefreshing) return;
            this.isRefreshing = true;
            try {
                const res = await fetch(`${API_BASE}/integrations/strava/import/refresh`, {
                    method: 'POST'
                });
                const data = await res.json();
                if (!res.ok) {
                    const detail = data?.detail || 'Strava refresh failed';
                    console.error('Strava refresh failed:', detail);
                    return;
                }

                await this.fetchData();
                console.log('Strava refresh done', data);
            } catch (e) {
                console.error('Failed to refresh Strava activities', e);
            } finally {
                this.isRefreshing = false;
            }
        },

        async sendChatMessage() {
            const text = (this.chatInput || '').trim();
            if (!text || this.isChatLoading) return;
            const composedText = text;

            this.chatError = '';
            this.chatMessages.push({ role: 'user', content: composedText });
            this.chatInput = '';
            this.isChatLoading = true;

            try {
                await this.persistChatMessage('user', composedText);

                    const payload = {
                        query: composedText,
                        model: this.selectedChatModel,
                        deterministic: true,
                        include_context_in_response: true,
                        include_input_preview: true
                    };

                this.chatDebug.lastQuery = composedText;
                this.chatDebug.lastPayload = payload;

                const res = await fetch(`${API_BASE}/llm/interpret`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                const data = await res.json();
                if (!res.ok) {
                    const detail = data?.detail || 'LLM request failed';
                    throw new Error(detail);
                }

                this.chatMessages.push({
                    role: 'assistant',
                    content: data?.answer || 'No response.'
                });
                await this.persistChatMessage('assistant', data?.answer || 'No response.');

                this.chatDebug.lastRawResponse = data;
                this.chatDebug.lastModelInput = data?.input_preview?.user_message || null;
                this.chatDebug.lastMcpTrace = data?.mcp_trace || data?.context?.mcp_trace || null;
                this.chatDebug.lastAudit = data?.audit || null;
            } catch (e) {
                this.chatError = e?.message || 'Failed to send message.';
            } finally {
                this.isChatLoading = false;
            }
        },

        formatDebugJson(value) {
            if (value === null || value === undefined) return '';
            try {
                return JSON.stringify(value, null, 2);
            } catch {
                return String(value);
            }
        },

        renderCoachMarkdown(text) {
            const source = String(text || '');
            const escaped = escapeHtml(source);

            const blocks = escaped.split(/\n\n+/);
            const htmlBlocks = blocks.map((block) => {
                const trimmed = block.trim();
                if (!trimmed) return '';

                const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
                if (headingMatch) {
                    const level = Math.min(6, headingMatch[1].length);
                    const content = this.renderMarkdownInline(headingMatch[2].trim());
                    if (level <= 2) {
                        return `<h${level} class="font-semibold text-gray-900 mt-3 mb-1">${content}</h${level}>`;
                    }
                    if (level === 3) {
                        return `<h3 class="font-semibold text-gray-900 mt-2 mb-1">${content}</h3>`;
                    }
                    if (level === 4) {
                        return `<h4 class="font-medium text-gray-900 mt-2 mb-1">${content}</h4>`;
                    }
                    return `<h${level} class="font-medium text-gray-800 mt-1 mb-1">${content}</h${level}>`;
                }

                const listLines = trimmed.split('\n');
                const allUnordered = listLines.every((line) => /^\s*[-*]\s+/.test(line));
                if (allUnordered) {
                    const items = listLines
                        .map((line) => line.replace(/^\s*[-*]\s+/, '').trim())
                        .map((line) => `<li>${this.renderMarkdownInline(line)}</li>`)
                        .join('');
                    return `<ul class="list-disc pl-5 space-y-1">${items}</ul>`;
                }

                const allOrdered = listLines.every((line) => /^\s*\d+\.\s+/.test(line));
                if (allOrdered) {
                    const items = listLines
                        .map((line) => line.replace(/^\s*\d+\.\s+/, '').trim())
                        .map((line) => `<li>${this.renderMarkdownInline(line)}</li>`)
                        .join('');
                    return `<ol class="list-decimal pl-5 space-y-1">${items}</ol>`;
                }

                return `<p>${this.renderMarkdownInline(trimmed).replace(/\n/g, '<br>')}</p>`;
            });

            return htmlBlocks.join('');
        },

        renderMarkdownInline(text) {
            return text
                .replace(/`([^`]+)`/g, '<code class="bg-gray-100 px-1 rounded text-[12px]">$1</code>')
                .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
                .replace(/\*([^*]+)\*/g, '<em>$1</em>');
        }
    }));
});
