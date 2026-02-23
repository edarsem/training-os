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
        
        plan: {},
        editPlan: false,
        planForm: { description: '', target_distance_km: null, target_sessions: null },

        isSessionModalOpen: false,
        sessionForm: { id: null, date: '', start_time: null, time_str: '', type: 'run', duration_minutes: 60, distance_km: null, elevation_gain_m: null, perceived_intensity: null, notes: '' },

        isNoteModalOpen: false,
        noteForm: { date: '', note: '' },

        init() {
            this.updateWeekInfo();
            this.fetchData();
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

        getSessionsForDate(dateStr) {
            return this.summary.sessions.filter(s => s.date === dateStr);
        },

        getDayNote(dateStr) {
            return this.summary.day_notes.find(n => n.date === dateStr);
        },

        getTypeColor(type) {
            const colors = {
                run: 'bg-blue-500',
                trail: 'bg-green-600',
                hike: 'bg-amber-600',
                bike: 'bg-orange-500',
                strength: 'bg-purple-500',
                mobility: 'bg-teal-500',
                generic: 'bg-slate-500',
                other: 'bg-gray-500'
            };
            return colors[type] || colors.other;
        },

        formatTime(dateString) {
            if (!dateString) return '';
            const d = new Date(dateString);
            return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
        },

        formatSessionDetails(session) {
            let details = `${session.duration_minutes}m`;
            if (session.distance_km && ['run', 'trail', 'bike', 'hike'].includes(session.type)) details += ` | ${session.distance_km}km`;
            if (session.elevation_gain_m && ['run', 'trail', 'bike', 'hike'].includes(session.type)) details += ` | ${session.elevation_gain_m}m+`;
            return details;
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
            this.sessionForm = { id: null, date: dateStr, start_time: null, time_str: '', type: 'run', duration_minutes: 60, distance_km: null, elevation_gain_m: null, perceived_intensity: null, notes: '' };
            this.isSessionModalOpen = true;
        },

        editSession(session) {
            this.sessionForm = { ...session, time_str: session.start_time ? this.formatTime(session.start_time) : '' };
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
            
            if (payload.time_str) {
                // Combine date and time_str into a valid ISO string
                payload.start_time = new Date(`${payload.date}T${payload.time_str}:00`).toISOString();
            } else {
                payload.start_time = null;
            }
            delete payload.time_str;

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
        }
    }));
});
