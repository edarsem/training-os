const API_BASE = 'http://localhost:8000/api';

// Leaflet/Chart.js instances live outside Alpine reactive data:
// Alpine's proxies break their internals.
let map = null;
let trackPolyline = null;
let hoverDot = null;
let leafletMarkers = [];
let actualPolyline = null;
let elevationChart = null;
let histogramChart = null;
let comparisonChart = null;
let currentTrack = null;

const SLOPE_COLORS = [
    { max: -40, color: '#172554' },
    { max: -35, color: '#1e3a8a' },
    { max: -30, color: '#1e40af' },
    { max: -25, color: '#1d4ed8' },
    { max: -20, color: '#2563eb' },
    { max: -15, color: '#3b82f6' },
    { max: -10, color: '#60a5fa' },
    { max: -5, color: '#93c5fd' },
    { max: -2, color: '#bbf7d0' },
    { max: 2, color: '#22c55e' },
    { max: 5, color: '#fde047' },
    { max: 10, color: '#f97316' },
    { max: 15, color: '#dc2626' },
    { max: 20, color: '#991b1b' },
    { max: 25, color: '#7f1d1d' },
    { max: 30, color: '#601414' },
    { max: 35, color: '#450a0a' },
    { max: 40, color: '#2d0606' },
    { max: Infinity, color: '#180303' },
];

function colorForSlope(slope) {
    for (const bracket of SLOPE_COLORS) {
        if (slope < bracket.max) return bracket.color;
    }
    return SLOPE_COLORS[SLOPE_COLORS.length - 1].color;
}

function destroyVisuals() {
    if (map) { map.remove(); map = null; }
    trackPolyline = null;
    actualPolyline = null;
    hoverDot = null;
    leafletMarkers = [];
    if (elevationChart) { elevationChart.destroy(); elevationChart = null; }
    if (histogramChart) { histogramChart.destroy(); histogramChart = null; }
    if (comparisonChart) { comparisonChart.destroy(); comparisonChart = null; }
    currentTrack = null;
}

function nearestTrackIndex(lat, lng) {
    if (!currentTrack) return 0;
    let best = 0;
    let bestDist = Infinity;
    for (let i = 0; i < currentTrack.n; i++) {
        const dLat = currentTrack.lat[i] - lat;
        const dLng = (currentTrack.lng[i] - lng) * Math.cos(lat * Math.PI / 180);
        const d = dLat * dLat + dLng * dLng;
        if (d < bestDist) { bestDist = d; best = i; }
    }
    return best;
}

function showHoverAtIndex(index) {
    if (!currentTrack || index === null || index < 0 || index >= currentTrack.n) return;
    if (hoverDot) {
        hoverDot.setLatLng([currentTrack.lat[index], currentTrack.lng[index]]);
        hoverDot.setStyle({ opacity: 1, fillOpacity: 1 });
    }
}

function hideHover() {
    if (hoverDot) hoverDot.setStyle({ opacity: 0, fillOpacity: 0 });
    if (elevationChart) {
        elevationChart.setActiveElements([]);
        elevationChart.tooltip.setActiveElements([], { x: 0, y: 0 });
        elevationChart.update('none');
    }
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
    Alpine.data('routePage', () => ({
        routes: [],
        selectedRouteId: '',
        route: null,
        isUploading: false,
        uploadError: '',

        addMode: null, // 'ravito' | 'note' | null
        isMarkerModalOpen: false,
        markerForm: { id: null, kind: 'ravito', distance_km: 0, label: '', note: '' },

        routeNotes: '',
        notesSaveState: '',
        notesSaveTimer: null,

        comparison: null,
        matchSessionId: '',
        matchDate: '',
        matchCandidates: [],
        isMatching: false,
        matchError: '',

        chatMessages: [],
        chatInput: '',
        chatError: '',
        isChatLoading: false,
        markdownConfigured: false,

        async init() {
            await this.fetchRoutes();
            if (this.routes.length > 0) {
                this.selectedRouteId = String(this.routes[0].id);
                await this.loadRoute(this.selectedRouteId);
            }
        },

        async fetchRoutes() {
            try {
                const res = await fetch(`${API_BASE}/routes`);
                this.routes = res.ok ? await res.json() : [];
            } catch (e) {
                console.error('Failed to fetch routes', e);
                this.routes = [];
            }
        },

        async loadRoute(routeId) {
            destroyVisuals();
            this.route = null;
            this.addMode = null;
            this.chatMessages = [];
            this.chatError = '';
            this.comparison = null;
            this.matchSessionId = '';
            this.matchDate = '';
            this.matchCandidates = [];
            this.matchError = '';
            if (!routeId) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${routeId}`);
                if (!res.ok) return;
                const data = await res.json();
                this.route = data;
                this.routeNotes = data.notes || '';
                this.notesSaveState = '';
                currentTrack = data.track;
                this.$nextTick(() => {
                    this.renderMap();
                    if (data.has_elevation) {
                        this.renderElevationChart();
                        this.renderHistogramChart();
                    }
                });
                if (data.session_id !== null) {
                    await this.fetchComparison();
                }
            } catch (e) {
                console.error('Failed to load route', e);
            }
        },

        async uploadGpx(event) {
            const file = event.target.files && event.target.files[0];
            if (!file) return;
            this.uploadError = '';
            this.isUploading = true;
            try {
                const formData = new FormData();
                formData.append('file', file);
                const res = await fetch(`${API_BASE}/routes/upload`, { method: 'POST', body: formData });
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || 'Upload failed');
                await this.fetchRoutes();
                this.selectedRouteId = String(data.id);
                await this.loadRoute(this.selectedRouteId);
            } catch (e) {
                this.uploadError = e?.message || 'Upload failed';
            } finally {
                this.isUploading = false;
                event.target.value = '';
            }
        },

        async deleteRoute() {
            if (!this.route) return;
            if (!confirm(`Delete route "${this.route.name}" and all its markers?`)) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}`, { method: 'DELETE' });
                if (!res.ok) return;
                destroyVisuals();
                this.route = null;
                this.selectedRouteId = '';
                await this.fetchRoutes();
            } catch (e) {
                console.error('Failed to delete route', e);
            }
        },

        // --- Map ---
        renderMap() {
            const container = document.getElementById('route-map');
            if (!container || !currentTrack) return;

            map = L.map(container);
            L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '&copy; OpenStreetMap contributors',
            }).addTo(map);

            const latlngs = currentTrack.lat.map((lat, i) => [lat, currentTrack.lng[i]]);
            trackPolyline = L.polyline(latlngs, { color: '#2563eb', weight: 4 }).addTo(map);
            map.fitBounds(trackPolyline.getBounds(), { padding: [30, 30] });

            L.circleMarker(latlngs[0], { radius: 7, color: '#16a34a', fillColor: '#16a34a', fillOpacity: 1 })
                .bindTooltip('Start').addTo(map);
            L.circleMarker(latlngs[latlngs.length - 1], { radius: 7, color: '#dc2626', fillColor: '#dc2626', fillOpacity: 1 })
                .bindTooltip('Finish').addTo(map);

            hoverDot = L.circleMarker(latlngs[0], {
                radius: 6, color: '#111827', fillColor: '#fbbf24', fillOpacity: 0, opacity: 0, weight: 2,
            }).addTo(map);

            map.on('mousemove', (e) => {
                const idx = nearestTrackIndex(e.latlng.lat, e.latlng.lng);
                showHoverAtIndex(idx);
                if (elevationChart) {
                    elevationChart.setActiveElements([{ datasetIndex: 0, index: idx }]);
                    elevationChart.tooltip.setActiveElements([{ datasetIndex: 0, index: idx }], { x: 0, y: 0 });
                    elevationChart.update('none');
                }
            });
            map.on('mouseout', hideHover);
            map.on('click', (e) => {
                if (!this.addMode) return;
                const idx = nearestTrackIndex(e.latlng.lat, e.latlng.lng);
                this.openMarkerForm(this.addMode, currentTrack.dist_km[idx]);
            });

            this.renderMapMarkers();
        },

        renderMapMarkers() {
            if (!map) return;
            leafletMarkers.forEach((m) => map.removeLayer(m));
            leafletMarkers = [];
            for (const m of (this.route?.markers || [])) {
                if (m.lat === null || m.lng === null) continue;
                const icon = L.divIcon({
                    className: m.kind === 'ravito' ? 'marker-icon-ravito' : 'marker-icon-note',
                    html: m.kind === 'ravito' ? '🥤' : '📝',
                    iconSize: [24, 24],
                    iconAnchor: [12, 12],
                });
                const tooltip = `km ${Number(m.distance_km).toFixed(1)}${m.label ? ' — ' + escapeHtml(m.label) : ''}`;
                const lm = L.marker([m.lat, m.lng], { icon }).bindTooltip(tooltip).addTo(map);
                leafletMarkers.push(lm);
            }
            this.updateMarkerOverlayDataset();
        },

        panToMarker(m) {
            if (map && m.lat !== null && m.lng !== null) {
                map.panTo([m.lat, m.lng]);
            }
        },

        // --- Charts ---
        renderElevationChart() {
            const canvas = document.getElementById('elevation-chart');
            if (!canvas || !currentTrack || !currentTrack.ele_m) return;
            const track = currentTrack;
            const self = this;

            elevationChart = new Chart(canvas, {
                type: 'line',
                data: {
                    labels: track.dist_km,
                    datasets: [
                        {
                            label: 'Elevation (m)',
                            data: track.ele_m,
                            pointRadius: 0,
                            borderWidth: 3,
                            fill: true,
                            backgroundColor: 'rgba(148, 163, 184, 0.15)',
                            tension: 0.1,
                            segment: {
                                borderColor: (ctx) => colorForSlope(track.slope_pct[ctx.p1DataIndex] ?? 0),
                            },
                        },
                        {
                            type: 'scatter',
                            label: 'Markers',
                            data: [],
                            pointRadius: 7,
                            pointHoverRadius: 9,
                            pointStyle: 'rectRot',
                            backgroundColor: '#f59e0b',
                            borderColor: '#92400e',
                            borderWidth: 1,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: 'index', intersect: false },
                    onHover: (event, elements, chart) => {
                        const points = chart.getElementsAtEventForMode(event, 'index', { intersect: false }, true);
                        if (points.length > 0) showHoverAtIndex(points[0].index);
                    },
                    onClick: (event, elements, chart) => {
                        if (!self.addMode) return;
                        const points = chart.getElementsAtEventForMode(event, 'index', { intersect: false }, true);
                        if (points.length > 0) self.openMarkerForm(self.addMode, track.dist_km[points[0].index]);
                    },
                    scales: {
                        x: {
                            type: 'linear',
                            title: { display: true, text: 'Distance (km)' },
                            min: 0,
                            max: track.dist_km[track.dist_km.length - 1],
                        },
                        y: { title: { display: true, text: 'Elevation (m)' } },
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                title: (items) => `km ${Number(items[0].parsed.x).toFixed(2)}`,
                                label: (item) => {
                                    if (item.datasetIndex === 1) {
                                        const m = item.raw.marker;
                                        return `${m.kind === 'ravito' ? '🥤' : '📝'} ${m.label || m.kind}`;
                                    }
                                    const slope = Math.round(track.slope_pct[item.dataIndex]);
                                    return `${Math.round(item.parsed.y)} m · ${slope > 0 ? '+' : ''}${slope}%`;
                                },
                            },
                        },
                    },
                },
            });

            canvas.addEventListener('mouseleave', hideHover);
            this.updateMarkerOverlayDataset();
        },

        updateMarkerOverlayDataset() {
            if (!elevationChart || !currentTrack || !currentTrack.ele_m) return;
            const intervalKm = (currentTrack.interval_m || 20) / 1000;
            const data = (this.route?.markers || []).map((m) => {
                const idx = Math.min(currentTrack.n - 1, Math.max(0, Math.round(Number(m.distance_km) / intervalKm)));
                return { x: Number(m.distance_km), y: currentTrack.ele_m[idx], marker: m };
            });
            elevationChart.data.datasets[1].data = data;
            elevationChart.update('none');
        },

        renderHistogramChart() {
            const canvas = document.getElementById('histogram-chart');
            if (!canvas || !this.route) return;
            const histogram = (this.route.slope_histogram || []).filter((b) => b.km > 0);

            histogramChart = new Chart(canvas, {
                type: 'bar',
                data: {
                    labels: histogram.map((b) => b.label),
                    datasets: [{
                        label: 'km',
                        data: histogram.map((b) => b.km),
                        backgroundColor: histogram.map((b) => colorForSlope(
                            b.min_pct === null ? -45 : b.max_pct === null ? 45 : (b.min_pct + b.max_pct) / 2
                        )),
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    scales: {
                        y: { title: { display: true, text: 'km' } },
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: (item) => {
                                    const b = histogram[item.dataIndex];
                                    return `${b.km} km (${b.pct_of_route}% of route)`;
                                },
                            },
                        },
                    },
                },
            });
        },

        // --- Markers ---
        sortedMarkers() {
            return [...(this.route?.markers || [])].sort((a, b) => Number(a.distance_km) - Number(b.distance_km));
        },

        toggleAddMode(kind) {
            this.addMode = this.addMode === kind ? null : kind;
        },

        openMarkerForm(kind, distanceKm) {
            this.markerForm = {
                id: null,
                kind,
                distance_km: Number(Number(distanceKm).toFixed(2)),
                label: '',
                note: '',
            };
            this.isMarkerModalOpen = true;
        },

        editMarker(m) {
            this.markerForm = {
                id: m.id,
                kind: m.kind,
                distance_km: Number(m.distance_km),
                label: m.label || '',
                note: m.note || '',
            };
            this.isMarkerModalOpen = true;
        },

        async saveMarker() {
            if (!this.route) return;
            const payload = {
                kind: this.markerForm.kind,
                distance_km: Number(this.markerForm.distance_km),
                label: (this.markerForm.label || '').trim() || null,
                note: (this.markerForm.note || '').trim() || null,
            };
            const isEdit = !!this.markerForm.id;
            const url = isEdit
                ? `${API_BASE}/routes/${this.route.id}/markers/${this.markerForm.id}`
                : `${API_BASE}/routes/${this.route.id}/markers`;
            try {
                const res = await fetch(url, {
                    method: isEdit ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    throw new Error(data?.detail || 'Failed to save marker');
                }
                this.isMarkerModalOpen = false;
                this.addMode = null;
                await this.refreshMarkers();
            } catch (e) {
                console.error('Failed to save marker', e);
                alert(e?.message || 'Failed to save marker');
            }
        },

        async deleteMarker(m) {
            if (!this.route) return;
            if (!confirm('Delete this marker?')) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}/markers/${m.id}`, { method: 'DELETE' });
                if (res.ok) await this.refreshMarkers();
            } catch (e) {
                console.error('Failed to delete marker', e);
            }
        },

        async refreshMarkers() {
            if (!this.route) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}`);
                if (!res.ok) return;
                const data = await res.json();
                this.route.markers = data.markers;
                this.renderMapMarkers();
            } catch (e) {
                console.error('Failed to refresh markers', e);
            }
        },

        // --- Strava match / comparison ---
        async fetchSessionsForDate() {
            this.matchCandidates = [];
            this.matchSessionId = '';
            if (!this.matchDate) return;
            try {
                const res = await fetch(`${API_BASE}/sessions?start_date=${this.matchDate}&end_date=${this.matchDate}`);
                if (res.ok) this.matchCandidates = await res.json();
            } catch (e) {
                console.error('Failed to fetch sessions for date', e);
            }
        },

        async matchSession() {
            if (!this.route || !this.matchSessionId || this.isMatching) return;
            this.matchError = '';
            this.isMatching = true;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}/match-session`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: Number(this.matchSessionId) }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || 'Match failed');
                this.route.session_id = data.session_id;
                this.applyComparison(data);
            } catch (e) {
                this.matchError = e?.message || 'Match failed';
            } finally {
                this.isMatching = false;
            }
        },

        async fetchComparison() {
            if (!this.route) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}/comparison`);
                if (!res.ok) return;
                this.applyComparison(await res.json());
            } catch (e) {
                console.error('Failed to fetch comparison', e);
            }
        },

        applyComparison(data) {
            this.comparison = data;
            this.$nextTick(() => {
                this.renderActualPolyline();
                this.renderComparisonChart();
            });
        },

        async unlinkSession() {
            if (!this.route) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}/match-session`, { method: 'DELETE' });
                if (!res.ok) return;
                this.route.session_id = null;
                this.comparison = null;
                if (actualPolyline && map) { map.removeLayer(actualPolyline); actualPolyline = null; }
                if (comparisonChart) { comparisonChart.destroy(); comparisonChart = null; }
            } catch (e) {
                console.error('Failed to unlink session', e);
            }
        },

        renderActualPolyline() {
            if (!map || !this.comparison) return;
            if (actualPolyline) { map.removeLayer(actualPolyline); actualPolyline = null; }
            const latlng = this.comparison.actual_latlng || [];
            if (latlng.length < 2) return;
            actualPolyline = L.polyline(latlng, { color: '#dc2626', weight: 3, dashArray: '6 6', opacity: 0.8 })
                .bindTooltip('Actual activity').addTo(map);
        },

        renderComparisonChart() {
            const canvas = document.getElementById('comparison-chart');
            if (!canvas || !currentTrack || !this.comparison) return;
            if (comparisonChart) { comparisonChart.destroy(); comparisonChart = null; }
            const self = this;
            const dist = currentTrack.dist_km;

            comparisonChart = new Chart(canvas, {
                type: 'line',
                data: {
                    labels: dist,
                    datasets: [
                        {
                            label: 'Pace (min/km)',
                            data: this.comparison.pace_min_per_km,
                            yAxisID: 'pace',
                            borderColor: '#2563eb',
                            pointRadius: 0,
                            borderWidth: 2,
                            spanGaps: true,
                        },
                        {
                            label: 'HR (bpm)',
                            data: this.comparison.hr_bpm,
                            yAxisID: 'hr',
                            borderColor: '#dc2626',
                            pointRadius: 0,
                            borderWidth: 1.5,
                            spanGaps: true,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: 'index', intersect: false },
                    onHover: (event, elements, chart) => {
                        const points = chart.getElementsAtEventForMode(event, 'index', { intersect: false }, true);
                        if (points.length > 0) showHoverAtIndex(points[0].index);
                    },
                    scales: {
                        x: {
                            type: 'linear',
                            title: { display: true, text: 'Distance (km)' },
                            min: 0,
                            max: dist[dist.length - 1],
                        },
                        pace: {
                            position: 'left',
                            reverse: true,
                            title: { display: true, text: 'Pace (min/km)' },
                        },
                        hr: {
                            position: 'right',
                            grid: { drawOnChartArea: false },
                            title: { display: true, text: 'HR (bpm)' },
                        },
                    },
                    plugins: {
                        tooltip: {
                            callbacks: {
                                title: (items) => `km ${Number(items[0].parsed.x).toFixed(2)}`,
                                label: (item) => item.datasetIndex === 0
                                    ? `Pace ${self.formatPace(item.parsed.y)}`
                                    : `HR ${Math.round(item.parsed.y)} bpm`,
                            },
                        },
                    },
                },
            });
            canvas.addEventListener('mouseleave', hideHover);
        },

        formatPace(pace) {
            if (pace === null || pace === undefined || !isFinite(pace)) return '—';
            const minutes = Math.floor(pace);
            const seconds = Math.round((pace - minutes) * 60);
            return `${minutes}'${String(seconds).padStart(2, '0')}"/km`;
        },

        // --- Global notes ---
        debouncedSaveNotes() {
            this.notesSaveState = '…';
            clearTimeout(this.notesSaveTimer);
            this.notesSaveTimer = setTimeout(() => this.saveNotes(), 800);
        },

        async saveNotes() {
            if (!this.route) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ notes: this.routeNotes }),
                });
                this.notesSaveState = res.ok ? 'saved' : 'save failed';
                if (res.ok) this.route.notes = this.routeNotes;
            } catch (e) {
                this.notesSaveState = 'save failed';
            }
        },

        // --- Coach chat (route-aware, ephemeral) ---
        async sendChatMessage() {
            const text = (this.chatInput || '').trim();
            if (!text || this.isChatLoading || !this.route) return;

            this.chatError = '';
            this.chatMessages.push({ role: 'user', content: text });
            this.chatInput = '';
            this.isChatLoading = true;

            try {
                const payload = {
                    query: text,
                    route_id: this.route.id,
                    deterministic: true,
                    include_context_in_response: false,
                    conversation_history: this.chatMessages
                        .slice(0, -1)
                        .filter((m) => m && (m.role === 'user' || m.role === 'assistant') && String(m.content || '').trim())
                        .map((m) => ({ role: m.role, content: String(m.content || '').trim() })),
                };

                const res = await fetch(`${API_BASE}/llm/interpret`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || 'LLM request failed');

                this.chatMessages.push({ role: 'assistant', content: data?.answer || 'No response.' });
            } catch (e) {
                this.chatError = e?.message || 'Failed to send message.';
            } finally {
                this.isChatLoading = false;
            }
        },

        renderCoachMarkdown(text) {
            const source = String(text || '');
            const markedLib = window.marked;
            const purifier = window.DOMPurify;
            if (!markedLib || !purifier) {
                return `<pre>${escapeHtml(source)}</pre>`;
            }
            if (!this.markdownConfigured && typeof markedLib.setOptions === 'function') {
                markedLib.setOptions({ gfm: true, breaks: true, headerIds: false, mangle: false });
                this.markdownConfigured = true;
            }
            const parsed = typeof markedLib.parse === 'function' ? markedLib.parse(source) : markedLib(source);
            return purifier.sanitize(parsed, { USE_PROFILES: { html: true } });
        },
    }));
});
