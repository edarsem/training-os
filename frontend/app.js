const API_BASE = 'http://localhost:8000/api';

document.addEventListener('alpine:init', () => {
    Alpine.data('app', () => ({
        currentDate: new Date(),
        currentYear: 0,
        currentWeek: 0,
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
        sessionForm: { id: null, date: '', type: 'run', duration_minutes: 60, distance_km: null, elevation_gain_m: null, perceived_intensity: null, notes: '' },

        isNoteModalOpen: false,
        noteForm: { date: '', note: '' },

        init() {
            this.updateWeekInfo();
            this.fetchData();
        },

        updateWeekInfo() {
            // Using date-fns (available globally via CDN)
            this.currentYear = dateFns.getISOYear(this.currentDate);
            this.currentWeek = dateFns.getISOWeek(this.currentDate);
            
            const start = dateFns.startOfISOWeek(this.currentDate);
            this.weekDays = Array.from({ length: 7 }).map((_, i) => {
                const d = dateFns.addDays(start, i);
                return {
                    date: d,
                    dateStr: dateFns.format(d, 'yyyy-MM-dd'),
                    dayName: dateFns.format(d, 'EEE'),
                    dayNumber: dateFns.format(d, 'd')
                };
            });
        },

        prevWeek() {
            this.currentDate = dateFns.subWeeks(this.currentDate, 1);
            this.updateWeekInfo();
            this.fetchData();
        },

        nextWeek() {
            this.currentDate = dateFns.addWeeks(this.currentDate, 1);
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
                strength: 'bg-purple-500',
                mobility: 'bg-teal-500',
                other: 'bg-gray-500'
            };
            return colors[type] || colors.other;
        },

        formatSessionDetails(session) {
            let details = `${session.duration_minutes}m`;
            if (session.distance_km) details += ` | ${session.distance_km}km`;
            if (session.elevation_gain_m) details += ` | ${session.elevation_gain_m}m+`;
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
            this.sessionForm = { id: null, date: dateStr, type: 'run', duration_minutes: 60, distance_km: null, elevation_gain_m: null, perceived_intensity: null, notes: '' };
            this.isSessionModalOpen = true;
        },

        editSession(session) {
            this.sessionForm = { ...session };
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
