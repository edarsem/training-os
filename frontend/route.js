const API_BASE = 'http://localhost:8000/api';

// Leaflet/Chart.js instances live outside Alpine reactive data:
// Alpine's proxies break their internals.
let map = null;
let trackPolyline = null;
let hoverDot = null;
let leafletMarkers = [];
let actualPolyline = null;
let highlightLayer = null;
let elevationChart = null;
let histogramChart = null;
let currentTrack = null;
let plannedTraceVisible = true;

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
    highlightLayer = null;
    hoverDot = null;
    leafletMarkers = [];
    if (elevationChart) { elevationChart.destroy(); elevationChart = null; }
    if (histogramChart) { histogramChart.destroy(); histogramChart = null; }
    currentTrack = null;
    plannedTraceVisible = true;
}

function clearBracketHighlight() {
    if (highlightLayer && map) { map.removeLayer(highlightLayer); highlightLayer = null; }
    if (trackPolyline) trackPolyline.setStyle({ opacity: plannedTraceVisible ? 1 : 0 });
}

function highlightBracketOnMap(bracket) {
    if (!map || !currentTrack || !currentTrack.slope_pct) return;
    clearBracketHighlight();
    if (plannedTraceVisible) trackPolyline.setStyle({ opacity: 0.25 });

    const lo = bracket.min_pct;
    const hi = bracket.max_pct;
    const color = colorForSlope((lo + hi) / 2);
    const segments = [];
    let run = null;
    for (let i = 0; i < currentTrack.n; i++) {
        const s = currentTrack.slope_pct[i];
        if (s >= lo && s <= hi) {
            if (!run) run = [];
            run.push([currentTrack.lat[i], currentTrack.lng[i]]);
        } else if (run) {
            if (run.length > 1) segments.push(run);
            run = null;
        }
    }
    if (run && run.length > 1) segments.push(run);

    highlightLayer = L.layerGroup(
        segments.map((seg) => L.polyline(seg, { color, weight: 6, opacity: 1 }))
    ).addTo(map);
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

        showNewFromActivity: false,
        raceSessions: [],
        newFromSessionId: '',
        newFromDate: '',
        newFromCandidates: [],
        isCreatingFromSession: false,
        newFromError: '',
        showPaceOverlay: false,
        showHrOverlay: false,

        chatMessages: [],
        chatInput: '',
        chatError: '',
        isChatLoading: false,
        markdownConfigured: false,
        chatModelOptions: [
            'mistral-small-latest',
            'mistral-medium-latest',
            'mistral-large-latest',
            'gemini-3.1-flash-lite-preview',
            'gemini-3.1-pro-preview',
            'gemini-3.1-pro-preview-customtools',
        ],
        selectedChatModel: 'mistral-small-latest',

        showPlannedTrace: true,
        showActualTrace: true,

        async init() {
            const stored = localStorage.getItem('training_os_chat_model');
            if (stored && this.chatModelOptions.includes(stored)) {
                this.selectedChatModel = stored;
            }
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
            this.showPaceOverlay = false;
            this.showHrOverlay = false;
            this.showPlannedTrace = true;
            this.showActualTrace = true;
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

        async toggleNewFromActivity() {
            this.showNewFromActivity = !this.showNewFromActivity;
            this.newFromError = '';
            if (this.showNewFromActivity && this.raceSessions.length === 0) {
                try {
                    const res = await fetch(`${API_BASE}/sessions/races`);
                    if (res.ok) {
                        const linked = new Set(this.routes.map((r) => r.session_id).filter((id) => id !== null));
                        this.raceSessions = (await res.json()).reverse().filter((s) => !linked.has(s.id));
                    }
                } catch (e) {
                    console.error('Failed to fetch race sessions', e);
                }
            }
        },

        async fetchSessionsForNewFromDate() {
            if (!this.newFromDate) { this.newFromCandidates = []; return; }
            this.newFromCandidates = null; // loading state
            try {
                const d = new Date(this.newFromDate);
                const minus1 = new Date(d); minus1.setDate(d.getDate() - 1);
                const plus1 = new Date(d); plus1.setDate(d.getDate() + 1);
                const fmt = (dt) => dt.toISOString().slice(0, 10);
                const res = await fetch(`${API_BASE}/sessions?start_date=${fmt(minus1)}&end_date=${fmt(plus1)}`);
                this.newFromCandidates = res.ok ? await res.json() : [];
            } catch (e) {
                this.newFromCandidates = [];
            }
        },

        async createFromSession() {
            if (!this.newFromSessionId || this.isCreatingFromSession) return;
            this.newFromError = '';
            this.isCreatingFromSession = true;
            try {
                const res = await fetch(`${API_BASE}/routes/from-session`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: Number(this.newFromSessionId) }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || 'Failed to create route from activity');
                this.showNewFromActivity = false;
                this.newFromSessionId = '';
                await this.fetchRoutes();
                this.selectedRouteId = String(data.id);
                await this.loadRoute(this.selectedRouteId);
            } catch (e) {
                this.newFromError = e?.message || 'Failed to create route from activity';
            } finally {
                this.isCreatingFromSession = false;
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
                lm.on('click', () => this.editMarker(m));
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
                        {
                            label: 'Pace (min/km)',
                            data: [],
                            yAxisID: 'pace',
                            borderColor: 'rgba(37, 99, 235, 0.8)',
                            pointRadius: 0,
                            borderWidth: 1.5,
                            spanGaps: true,
                            hidden: true,
                        },
                        {
                            label: 'HR (bpm)',
                            data: [],
                            yAxisID: 'hr',
                            borderColor: 'rgba(220, 38, 38, 0.7)',
                            pointRadius: 0,
                            borderWidth: 1.5,
                            spanGaps: true,
                            hidden: true,
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
                        pace: {
                            position: 'right',
                            reverse: true,
                            display: false,
                            title: { display: true, text: 'Pace (min/km)' },
                        },
                        hr: {
                            position: 'right',
                            display: false,
                            grid: { drawOnChartArea: false },
                            title: { display: true, text: 'HR (bpm)' },
                        },
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
                                    if (item.datasetIndex === 2) return `Moving pace ${self.formatPace(item.parsed.y)}`;
                                    if (item.datasetIndex === 3) return `HR ${Math.round(item.parsed.y)} bpm`;
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
            // steepest climbs at the top
            const histogram = (this.route.slope_histogram || []).filter((b) => b.km > 0).reverse();

            histogramChart = new Chart(canvas, {
                type: 'bar',
                data: {
                    labels: histogram.map((b) => b.label),
                    datasets: [{
                        label: 'km',
                        data: histogram.map((b) => b.km),
                        backgroundColor: histogram.map((b) => colorForSlope((b.min_pct + b.max_pct) / 2)),
                    }],
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    onHover: (event, elements) => {
                        if (elements.length > 0) {
                            highlightBracketOnMap(histogram[elements[0].index]);
                        } else {
                            clearBracketHighlight();
                        }
                    },
                    scales: {
                        x: { title: { display: true, text: 'km' }, ticks: { maxTicksLimit: 4 } },
                        y: { ticks: { font: { size: 10 } } },
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
            canvas.addEventListener('mouseleave', clearBracketHighlight);
        },

        // --- Markers ---
        sortedMarkers() {
            return [...(this.route?.markers || [])].sort((a, b) => Number(a.distance_km) - Number(b.distance_km));
        },

        toggleAddMode(kind) {
            this.addMode = this.addMode ? null : kind;
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
                this.updateOverlayData();
            });
        },

        async unlinkSession() {
            if (!this.route) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}/match-session`, { method: 'DELETE' });
                if (!res.ok) return;
                this.route.session_id = null;
                this.comparison = null;
                this.showPaceOverlay = false;
                this.showHrOverlay = false;
                if (actualPolyline && map) { map.removeLayer(actualPolyline); actualPolyline = null; }
                this.updateOverlayData();
            } catch (e) {
                console.error('Failed to unlink session', e);
            }
        },

        hasPlannedTrace() {
            // routes created from a Strava activity have no GPX file: their track IS the actual trace
            return !!(this.route && this.route.source_filename);
        },

        renderActualPolyline() {
            if (!map || !this.comparison) return;
            if (actualPolyline) { map.removeLayer(actualPolyline); actualPolyline = null; }
            if (!this.hasPlannedTrace()) return; // planned and actual would be the same trace
            const latlng = this.comparison.actual_latlng || [];
            if (latlng.length < 2) return;
            actualPolyline = L.polyline(latlng, { color: '#dc2626', weight: 3, dashArray: '6 6', opacity: 0.8 })
                .bindTooltip('Actual activity').addTo(map);
            this.updateTraceVisibility();
        },

        updateTraceVisibility() {
            plannedTraceVisible = !!this.showPlannedTrace;
            if (trackPolyline) trackPolyline.setStyle({ opacity: plannedTraceVisible ? 1 : 0 });
            if (actualPolyline) actualPolyline.setStyle({ opacity: this.showActualTrace ? 0.8 : 0 });
        },

        updateOverlayData() {
            if (!elevationChart) return;
            elevationChart.data.datasets[2].data = this.comparison ? this.comparison.pace_min_per_km : [];
            elevationChart.data.datasets[3].data = this.comparison ? this.comparison.hr_bpm : [];
            this.updateOverlayVisibility();
        },

        updateOverlayVisibility() {
            if (!elevationChart) return;
            const showPace = !!(this.comparison && this.showPaceOverlay);
            const showHr = !!(this.comparison && this.showHrOverlay);
            elevationChart.data.datasets[2].hidden = !showPace;
            elevationChart.data.datasets[3].hidden = !showHr;
            elevationChart.options.scales.pace.display = showPace;
            elevationChart.options.scales.hr.display = showHr;
            elevationChart.update('none');
        },

        formatPace(pace) {
            if (pace === null || pace === undefined || !isFinite(pace)) return '—';
            let minutes = Math.floor(pace);
            let seconds = Math.round((pace - minutes) * 60);
            if (seconds === 60) { minutes += 1; seconds = 0; }
            return `${minutes}'${String(seconds).padStart(2, '0')}"`;
        },

        formatSplitTime(seconds) {
            if (seconds === null || seconds === undefined || !isFinite(seconds)) return '—';
            const total = Math.round(seconds);
            const h = Math.floor(total / 3600);
            const m = Math.floor((total % 3600) / 60);
            const s = total % 60;
            return h > 0
                ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
                : `${m}:${String(s).padStart(2, '0')}`;
        },

        getProviderForModel(modelName) {
            const normalized = String(modelName || '').trim().toLowerCase();
            return normalized.startsWith('gemini') ? 'google' : 'mistral';
        },

        setChatModel(modelName) {
            if (!this.chatModelOptions.includes(modelName)) return;
            this.selectedChatModel = modelName;
            localStorage.setItem('training_os_chat_model', modelName);
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
            if (this.$refs.chatInput) { this.$refs.chatInput.style.height = 'auto'; }
            this.isChatLoading = true;
            this.$nextTick(() => {
                const el = this.$refs.chatMessages;
                if (el) el.scrollTop = el.scrollHeight;
            });

            try {
                const effectiveModel = this.chatModelOptions.includes(this.selectedChatModel)
                    ? this.selectedChatModel
                    : this.chatModelOptions[0];
                const payload = {
                    query: text,
                    route_id: this.route.id,
                    provider: this.getProviderForModel(effectiveModel),
                    model: effectiveModel,
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
                this.$nextTick(() => {
                    const el = this.$refs.chatMessages;
                    if (el) el.scrollTop = el.scrollHeight;
                });
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
