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
let plannedTraceBaseOpacity = 1;
let kmMarkers = [];
let pinnedDot = null;
let pinnedTrackIdx = null;
let cumulativeTimeS = null;
// The profile/overlay arrays the elevation chart currently renders. In 'distance' mode this is
// a view over currentTrack + comparison overlays (20 m grid); in 'time' mode it is the activity's
// time-sampled series (comparison.time_series), which represents pauses as flat plateaus.
// Hover/pin indices are indices into activeProfile, not currentTrack.
let activeProfile = null;

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
    plannedTraceBaseOpacity = 1;
    kmMarkers = [];
    pinnedDot = null;
    pinnedTrackIdx = null;
    cumulativeTimeS = null;
    activeProfile = null;
}

function clearBracketHighlight() {
    if (highlightLayer && map) { map.removeLayer(highlightLayer); highlightLayer = null; }
    if (trackPolyline) trackPolyline.setStyle({ opacity: plannedTraceVisible ? plannedTraceBaseOpacity : 0 });
}

function highlightBracketOnMap(bracket) {
    // highlight on the primary trace: activity slope/positions when Strava is primary, else planned
    const prof = (activeProfile && activeProfile.slope) ? activeProfile : currentTrack;
    const slope = prof === currentTrack ? currentTrack?.slope_pct : activeProfile.slope;
    if (!map || !prof || !slope) return;
    clearBracketHighlight();
    if (plannedTraceVisible && trackPolyline) trackPolyline.setStyle({ opacity: 0.25 });

    const lo = bracket.min_pct;
    const hi = bracket.max_pct;
    const color = colorForSlope((lo + hi) / 2);
    const segments = [];
    let run = null;
    for (let i = 0; i < prof.n; i++) {
        const s = slope[i];
        if (s !== null && s >= lo && s <= hi) {
            if (!run) run = [];
            run.push([prof.lat[i], prof.lng[i]]);
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

function nearestProfileIndex(lat, lng) {
    if (!activeProfile) return 0;
    let best = 0;
    let bestDist = Infinity;
    const la = activeProfile.lat;
    const lo = activeProfile.lng;
    for (let i = 0; i < activeProfile.n; i++) {
        if (la[i] == null || lo[i] == null) continue;
        const dLat = la[i] - lat;
        const dLng = (lo[i] - lng) * Math.cos(lat * Math.PI / 180);
        const d = dLat * dLat + dLng * dLng;
        if (d < bestDist) { bestDist = d; best = i; }
    }
    return best;
}

function profileIndexForDistance(distKm) {
    if (!activeProfile) return 0;
    const d = Number(distKm);
    let best = 0;
    let bestDiff = Infinity;
    for (let i = 0; i < activeProfile.n; i++) {
        const diff = Math.abs(activeProfile.distKm[i] - d);
        if (diff < bestDiff) { bestDiff = diff; best = i; }
    }
    return best;
}

function xyData(yArr) {
    if (!activeProfile || !yArr) return [];
    const out = [];
    for (let i = 0; i < activeProfile.n; i++) out.push({ x: activeProfile.x[i], y: yArr[i] ?? null });
    return out;
}

function showHoverAtIndex(index) {
    if (!activeProfile || index === null || index < 0 || index >= activeProfile.n) return;
    if (hoverDot && activeProfile.lat[index] != null) {
        hoverDot.setLatLng([activeProfile.lat[index], activeProfile.lng[index]]);
        hoverDot.setStyle({ opacity: 1, fillOpacity: 1 });
    }
}

function hideHover() {
    if (hoverDot) hoverDot.setStyle({ opacity: 0, fillOpacity: 0 });
    if (elevationChart) {
        elevationChart.setActiveElements([]);
        elevationChart.tooltip.setActiveElements([], { x: 0, y: 0 });
        elevationChart.update('none');
        if (pinnedTrackIdx !== null) {
            // Restore pinned tooltip after Chart.js finishes its own mouseleave cleanup
            setTimeout(() => {
                if (pinnedTrackIdx !== null && elevationChart) {
                    elevationChart.setActiveElements([{ datasetIndex: 0, index: pinnedTrackIdx }]);
                    elevationChart.tooltip.setActiveElements([{ datasetIndex: 0, index: pinnedTrackIdx }], { x: 0, y: 0 });
                    elevationChart.update('none');
                }
            }, 0);
        }
    }
}

function buildCumulativeTime(comparison, track) {
    if (!comparison || !comparison.km_splits || !track) return null;
    const splits = [...comparison.km_splits].sort((a, b) => a.km - b.km);
    const kmCum = { 0: 0 };
    let cum = 0;
    for (const s of splits) { cum += (s.duration_s || 0); kmCum[s.km] = cum; }
    const result = [];
    for (let i = 0; i < track.n; i++) {
        const d = track.dist_km[i];
        const km = Math.floor(d);
        const t0 = kmCum[km] ?? 0;
        const t1 = kmCum[km + 1] ?? t0;
        result.push(t0 + (d - km) * (t1 - t0));
    }
    return result;
}

function showPinnedAtIndex(idx) {
    if (!activeProfile || idx === null || idx < 0 || idx >= activeProfile.n) return;
    if (activeProfile.lat[idx] == null) return;
    const latlng = [activeProfile.lat[idx], activeProfile.lng[idx]];
    if (pinnedDot) {
        pinnedDot.setLatLng(latlng);
    } else if (map) {
        pinnedDot = L.circleMarker(latlng, {
            radius: 7, color: '#7c3aed', fillColor: '#a78bfa', fillOpacity: 0.9, weight: 2,
        }).addTo(map);
    }
}

function buildStopPositions(stops) {
    // Real stop segments from the backend, anchored by actual elapsed time + distance. The backend
    // already applies the single MIN_STOP_DURATION_S threshold, so every stop here is also counted
    // in the km-split stopped time — map/profile and table stay in sync.
    return stops || [];
}

const stopLinesPlugin = {
    id: 'stopLines',
    afterDraw(chart) {
        const stops = chart.$stopPositions;
        if (!stops || stops.length === 0) return;
        const ctx = chart.ctx;
        const xAxis = chart.scales.x;
        const { top, bottom } = chart.chartArea;
        const useTime = chart.$xAxisMode === 'time';
        const mins = (s) => Math.floor(s / 60);
        const secs = (s) => Math.round(s % 60);
        const fmtStop = (s) => mins(s) > 0 ? `${mins(s)}'${String(secs(s)).padStart(2, '0')}"` : `${secs(s)}"`;
        ctx.save();
        ctx.lineWidth = 1.5;
        for (const stop of stops) {
            const label = fmtStop(stop.duration_s);
            if (useTime && stop.t_start_s !== undefined) {
                const xEnd = xAxis.getPixelForValue(stop.t_end_s);
                const xStart = xAxis.getPixelForValue(stop.t_start_s);
                ctx.fillStyle = 'rgba(234, 179, 8, 0.12)';
                ctx.fillRect(xStart, top, xEnd - xStart, bottom - top);
                ctx.beginPath();
                ctx.setLineDash([3, 4]);
                ctx.strokeStyle = 'rgba(234, 179, 8, 0.7)';
                ctx.moveTo(xStart, top); ctx.lineTo(xStart, bottom); ctx.stroke();
                ctx.beginPath();
                ctx.moveTo(xEnd, top); ctx.lineTo(xEnd, bottom); ctx.stroke();
                ctx.setLineDash([]);
                ctx.font = 'bold 9px sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.fillStyle = 'rgba(161, 98, 7, 0.9)';
                ctx.fillText(label, (xStart + xEnd) / 2, top + 3);
            } else if (!useTime) {
                const x = xAxis.getPixelForValue(stop.dist_km);
                ctx.beginPath();
                ctx.setLineDash([3, 4]);
                ctx.strokeStyle = 'rgba(234, 179, 8, 0.7)';
                ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
                ctx.setLineDash([]);
                ctx.font = 'bold 9px sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.fillStyle = 'rgba(161, 98, 7, 0.9)';
                ctx.fillText(label, x, top + 3);
            }
        }
        ctx.restore();
    },
};

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

        pinnedDistanceKm: null,
        pinnedInfo: null,
        dataSource: 'gpx',
        stravaElevGain: 0,
        stravaElevLoss: 0,
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
        showCadenceOverlay: false,
        xAxisMode: 'distance',

        chatMessages: [],
        chatInput: '',
        chatError: '',
        isChatLoading: false,
        coachTextCopied: false,
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
            document.addEventListener('keydown', (e) => {
                if ((e.key === 'n' || e.key === 'N') &&
                    !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName || '')) {
                    this.openNoteAtPinned();
                }
            });
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
            this.pinnedDistanceKm = null;
            this.pinnedInfo = null;
            this.dataSource = 'gpx';
            this.stravaElevGain = 0;
            this.stravaElevLoss = 0;
            this.chatMessages = [];
            this.chatError = '';
            this.comparison = null;
            this.matchSessionId = '';
            this.matchDate = '';
            this.matchCandidates = [];
            this.matchError = '';
            this.showPaceOverlay = false;
            this.showHrOverlay = false;
            this.showCadenceOverlay = false;
            this.xAxisMode = 'distance';
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
                const idx = nearestProfileIndex(e.latlng.lat, e.latlng.lng);
                showHoverAtIndex(idx);
                if (elevationChart) {
                    elevationChart.setActiveElements([{ datasetIndex: 0, index: idx }]);
                    elevationChart.tooltip.setActiveElements([{ datasetIndex: 0, index: idx }], { x: 0, y: 0 });
                    elevationChart.update('none');
                }
            });
            map.on('mouseout', hideHover);
            map.on('click', (e) => {
                if (pinnedTrackIdx !== null) { this.clearPinned(); return; }
                const idx = nearestProfileIndex(e.latlng.lat, e.latlng.lng);
                this.setPinned(idx);
            });

            this.renderMapMarkers();
            this.renderKmMarkers();
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
                lm.on('click', (e) => { L.DomEvent.stopPropagation(e); this.editMarker(m); });
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
            this.rebuildActiveProfile();
            const self = this;

            elevationChart = new Chart(canvas, {
                type: 'line',
                data: {
                    datasets: [
                        {
                            label: 'Elevation (m)',
                            data: xyData(activeProfile.ele),
                            pointRadius: 0,
                            borderWidth: 3,
                            fill: false,
                            tension: 0.1,
                            segment: {
                                borderColor: (ctx) => colorForSlope(activeProfile?.slope?.[ctx.p1DataIndex] ?? 0),
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
                        {
                            label: 'Cadence (spm)',
                            data: [],
                            yAxisID: 'cadence',
                            borderColor: 'rgba(5, 150, 105, 0.75)',
                            pointRadius: 0,
                            borderWidth: 1.5,
                            spanGaps: true,
                            hidden: true,
                        },
                        {
                            type: 'scatter',
                            label: '_pin',
                            data: [],
                            pointRadius: 8,
                            pointHoverRadius: 8,
                            pointStyle: 'circle',
                            backgroundColor: 'rgba(167, 139, 250, 0.9)',
                            borderColor: '#7c3aed',
                            borderWidth: 2,
                        },
                    ],
                },
                plugins: [stopLinesPlugin],
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
                        const markerHits = chart.getElementsAtEventForMode(event, 'point', { intersect: true }, true)
                            .filter((el) => el.datasetIndex === 1);
                        if (markerHits.length > 0) {
                            const md = chart.data.datasets[1].data[markerHits[0].index];
                            if (md?.marker) { self.editMarker(md.marker); return; }
                        }
                        if (pinnedTrackIdx !== null) { self.clearPinned(); return; }
                        const points = chart.getElementsAtEventForMode(event, 'index', { intersect: false }, true);
                        if (points.length > 0) self.setPinned(points[0].index);
                    },
                    scales: {
                        x: {
                            type: 'linear',
                            title: { display: true, text: 'Distance (km)' },
                            min: 0,
                            max: activeProfile.x[activeProfile.n - 1],
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
                        cadence: {
                            position: 'right',
                            display: false,
                            grid: { drawOnChartArea: false },
                            title: { display: true, text: 'Cadence (spm)' },
                        },
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                title: (items) => {
                                    const idx = items[0].dataIndex;
                                    const km = activeProfile?.distKm?.[idx];
                                    const t = activeProfile?.timeS?.[idx];
                                    const tStr = t != null ? self.formatSplitTime(Math.round(t)) : null;
                                    if (self.xAxisMode === 'time') {
                                        let title = tStr ?? '—';
                                        if (km != null) title += ` · km ${Number(km).toFixed(2)}`;
                                        return title;
                                    }
                                    let title = km != null ? `km ${Number(km).toFixed(2)}` : '—';
                                    if (tStr) title += ` · ${tStr}`;
                                    return title;
                                },
                                label: (item) => {
                                    if (item.datasetIndex === 1) {
                                        const m = item.raw.marker;
                                        return `${m.kind === 'ravito' ? '🥤' : '📝'} ${m.label || m.kind}`;
                                    }
                                    if (item.datasetIndex === 2) return `Moving pace ${self.formatPace(item.parsed.y)}`;
                                    if (item.datasetIndex === 3) return `HR ${Math.round(item.parsed.y)} bpm`;
                                    if (item.datasetIndex === 4) return `Cadence ${Math.round(item.parsed.y)} spm`;
                                    if (item.datasetIndex === 5) return null;
                                    const slope = Math.round(activeProfile?.slope?.[item.dataIndex] ?? 0);
                                    return `${Math.round(item.parsed.y)} m · ${slope > 0 ? '+' : ''}${slope}%`;
                                },
                                afterBody: (items) => {
                                    const hoverIdx = items[0]?.dataIndex;
                                    if (pinnedTrackIdx === null || pinnedTrackIdx === hoverIdx) return [];
                                    const p = pinnedTrackIdx;
                                    const lines = ['─────────'];
                                    const t = activeProfile?.timeS?.[p];
                                    const km = activeProfile?.distKm?.[p];
                                    const tStr = t != null ? self.formatSplitTime(Math.round(t)) : null;
                                    if (self.xAxisMode === 'time') {
                                        lines.push(`📍 ${tStr ?? '—'}${km != null ? ` · km ${Number(km).toFixed(2)}` : ''}`);
                                    } else {
                                        lines.push(`📍 km ${km != null ? Number(km).toFixed(2) : '—'}`);
                                        if (tStr) lines.push(`   ${tStr}`);
                                    }
                                    if (activeProfile?.ele && activeProfile?.slope) {
                                        const sl = Math.round(activeProfile.slope[p]);
                                        lines.push(`   ${Math.round(activeProfile.ele[p])} m · ${sl > 0 ? '+' : ''}${sl}%`);
                                    }
                                    return lines;
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
            if (!elevationChart || !activeProfile || !activeProfile.ele) return;
            // Markers carry a lat/lng on the primary trace, so place them by geography on whichever
            // profile is shown (activity or planned overlay) — matching by distance number would
            // drift when the two traces differ in length. Fall back to distance only if no lat/lng.
            const data = (this.route?.markers || []).map((m) => {
                const idx = (m.lat != null && m.lng != null)
                    ? nearestProfileIndex(Number(m.lat), Number(m.lng))
                    : profileIndexForDistance(Number(m.distance_km));
                return { x: activeProfile.x[idx], y: activeProfile.ele[idx], marker: m };
            });
            elevationChart.data.datasets[1].data = data;
            elevationChart.update('none');
        },

        renderHistogramChart() {
            const canvas = document.getElementById('histogram-chart');
            if (!canvas || !this.route) return;
            if (histogramChart) { histogramChart.destroy(); histogramChart = null; }
            // gradient distribution of the primary trace (activity when Strava is primary)
            const source = (this.dataSource === 'strava' && this.comparison?.activity_slope_histogram?.length)
                ? this.comparison.activity_slope_histogram : (this.route.slope_histogram || []);
            // steepest climbs at the top
            const histogram = source.filter((b) => b.km > 0).reverse();

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

        setPinned(idx) {
            if (!activeProfile || idx === null || idx < 0 || idx >= activeProfile.n) return;
            pinnedTrackIdx = idx;
            this.pinnedDistanceKm = Number(activeProfile.distKm[idx]).toFixed(2);
            showPinnedAtIndex(idx);
            if (map && activeProfile.lat[idx] != null) {
                const latlng = L.latLng(activeProfile.lat[idx], activeProfile.lng[idx]);
                if (!map.getBounds().contains(latlng)) map.panTo(latlng);
            }
            const paceArr = activeProfile.pace;
            const hrArr = activeProfile.hr;
            const cadArr = activeProfile.cad;
            this.pinnedInfo = {
                time: activeProfile.timeS?.[idx] != null ? Math.round(activeProfile.timeS[idx]) : null,
                elevation: activeProfile.ele ? Math.round(activeProfile.ele[idx]) : null,
                slope: activeProfile.slope ? Math.round(activeProfile.slope[idx]) : null,
                pace: paceArr?.[idx] != null && isFinite(paceArr[idx]) ? paceArr[idx] : null,
                hr: hrArr?.[idx] != null ? Math.round(hrArr[idx]) : null,
                cadence: cadArr?.[idx] != null ? Math.round(cadArr[idx]) : null,
            };
            if (elevationChart && activeProfile.ele) {
                elevationChart.data.datasets[5].data = [{ x: activeProfile.x[idx], y: activeProfile.ele[idx] }];
                elevationChart.setActiveElements([{ datasetIndex: 0, index: idx }]);
                elevationChart.tooltip.setActiveElements([{ datasetIndex: 0, index: idx }], { x: 0, y: 0 });
                elevationChart.update('none');
            }
        },

        clearPinned() {
            pinnedTrackIdx = null;
            this.pinnedDistanceKm = null;
            this.pinnedInfo = null;
            if (pinnedDot && map) { map.removeLayer(pinnedDot); pinnedDot = null; }
            if (elevationChart) {
                elevationChart.data.datasets[5].data = [];
                elevationChart.setActiveElements([]);
                elevationChart.tooltip.setActiveElements([], { x: 0, y: 0 });
                elevationChart.update('none');
            }
        },

        openNoteAtPinned() {
            if (pinnedTrackIdx === null || !activeProfile || !this.route) return;
            const distKm = activeProfile.distKm[pinnedTrackIdx];
            // When the active profile IS the primary trace (activity, or the GPX of an unlinked
            // route), the pinned distance is exact — send it directly so a note placed at "km 10"
            // is stored at km 10. Only pinning on the planned overlay (GPX view of a linked route)
            // needs geographic snapping onto the primary trace.
            if (activeProfile.source === 'activity' || !this.comparison) {
                this.openMarkerForm('note', distKm);
            } else {
                const lat = activeProfile.lat?.[pinnedTrackIdx];
                const lng = activeProfile.lng?.[pinnedTrackIdx];
                this.openMarkerForm('note', distKm, lat, lng);
            }
        },

        setDataSource(source) {
            this.dataSource = source;
            this.updateTraceVisibility();
            // every analysis follows the primary trace: profile, histogram, km table, brackets, map
            if (elevationChart) this.setXAxisMode(this.xAxisMode);
            this.renderHistogramChart();
            this.renderKmMarkers();
        },

        // The km-split and gradient-bracket tables follow the primary trace: the activity's own
        // (full-length) analysis when Strava is primary, the planned-vs-actual alignment otherwise.
        displayKmSplits() {
            const c = this.comparison;
            if (!c) return [];
            return (this.dataSource === 'strava' && c.activity_km_splits) ? c.activity_km_splits : c.km_splits;
        },

        displayBracketStats() {
            const c = this.comparison;
            if (!c) return [];
            return (this.dataSource === 'strava' && c.activity_bracket_stats) ? c.activity_bracket_stats : c.bracket_stats;
        },

        renderKmMarkers() {
            if (!map) return;
            kmMarkers.forEach((m) => map.removeLayer(m));
            kmMarkers = [];
            // km marks along the primary trace (the activity when Strava is primary, else planned)
            const lat = activeProfile ? activeProfile.lat : currentTrack?.lat;
            const lng = activeProfile ? activeProfile.lng : currentTrack?.lng;
            const distKm = activeProfile ? activeProfile.distKm : currentTrack?.dist_km;
            const cnt = activeProfile ? activeProfile.n : currentTrack?.n;
            if (!lat || !distKm) return;
            const totalKm = distKm[cnt - 1];
            for (let km = 1; km <= Math.floor(totalKm); km++) {
                let idx = 0, best = Infinity;
                for (let i = 0; i < cnt; i++) {
                    const dd = Math.abs(distKm[i] - km);
                    if (dd < best) { best = dd; idx = i; }
                }
                const icon = L.divIcon({
                    className: '',
                    html: `<div style="background:rgba(255,255,255,0.85);border:1px solid #9ca3af;border-radius:8px;padding:1px 4px;font-size:9px;font-weight:700;color:#374151;white-space:nowrap;">${km}</div>`,
                    iconSize: null,
                    iconAnchor: [8, 8],
                });
                const m = L.marker([lat[idx], lng[idx]], { icon, interactive: false, zIndexOffset: -200 });
                m.addTo(map);
                kmMarkers.push(m);
            }
        },

        openMarkerForm(kind, distanceKm, lat = null, lng = null) {
            this.markerForm = {
                id: null,
                kind,
                distance_km: Number(Number(distanceKm).toFixed(2)),
                lat: lat ?? null,
                lng: lng ?? null,
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
                lat: null,
                lng: null,
                label: m.label || '',
                note: m.note || '',
            };
            this.isMarkerModalOpen = true;
        },

        // Editing the distance by hand drops any pinned lat/lng so the typed distance wins.
        onMarkerDistanceInput() {
            this.markerForm.lat = null;
            this.markerForm.lng = null;
        },

        async saveMarker() {
            if (!this.route) return;
            const payload = {
                kind: this.markerForm.kind,
                distance_km: Number(this.markerForm.distance_km),
                label: (this.markerForm.label || '').trim() || null,
                note: (this.markerForm.note || '').trim() || null,
            };
            // A pinned point carries lat/lng: anchor geographically on the primary trace.
            if (this.markerForm.lat != null && this.markerForm.lng != null) {
                payload.lat = Number(this.markerForm.lat);
                payload.lng = Number(this.markerForm.lng);
            }
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
                this.clearPinned();
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
                // linking adopts the Strava activity's name and (appended) description
                if (data.route_name) this.route.name = data.route_name;
                if (data.route_notes !== undefined && data.route_notes !== null) {
                    this.route.notes = data.route_notes;
                    this.routeNotes = data.route_notes;
                }
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
            if (this.hasPlannedTrace()) this.dataSource = 'strava';
            cumulativeTimeS = data.grid_elapsed_s || buildCumulativeTime(data, currentTrack);
            // activity's own elevation totals (full length) when available
            const elevSplits = data.activity_km_splits || data.km_splits;
            if (elevSplits) {
                this.stravaElevGain = elevSplits.reduce((s, k) => s + (k.d_plus_m || 0), 0);
                this.stravaElevLoss = elevSplits.reduce((s, k) => s + (k.d_minus_m || 0), 0);
            }
            this.$nextTick(() => {
                this.renderActualPolyline();
                this.updateOverlayData();   // rebuilds activeProfile to the primary trace
                this.renderHistogramChart();
                this.renderKmMarkers();
            });
        },

        async unlinkSession() {
            if (!this.route) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}/match-session`, { method: 'DELETE' });
                if (!res.ok) return;
                this.route.session_id = null;
                this.comparison = null;
                this.dataSource = 'gpx';
                this.stravaElevGain = 0;
                this.stravaElevLoss = 0;
                this.showPaceOverlay = false;
                this.showHrOverlay = false;
                this.showCadenceOverlay = false;
                this.xAxisMode = 'distance';
                cumulativeTimeS = null;
                if (actualPolyline && map) { map.removeLayer(actualPolyline); actualPolyline = null; }
                this.updateTraceVisibility();
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
            const stravaIsPrimary = this.hasPlannedTrace() && this.dataSource === 'strava';
            if (stravaIsPrimary) {
                plannedTraceBaseOpacity = 0.7;
                if (trackPolyline) trackPolyline.setStyle({ color: '#93c5fd', weight: 3, dashArray: '6 6', opacity: plannedTraceVisible ? 0.7 : 0 });
                if (actualPolyline) actualPolyline.setStyle({ color: '#2563eb', weight: 4, dashArray: '', opacity: this.showActualTrace ? 1 : 0 });
            } else {
                plannedTraceBaseOpacity = 1;
                if (trackPolyline) trackPolyline.setStyle({ color: '#2563eb', weight: 4, dashArray: '', opacity: plannedTraceVisible ? 1 : 0 });
                if (actualPolyline) actualPolyline.setStyle({ color: '#dc2626', weight: 3, dashArray: '6 6', opacity: this.showActualTrace ? 0.8 : 0 });
            }
        },

        rebuildActiveProfile() {
            const ts = this.comparison?.time_series;
            // Use the activity series whenever Strava is the primary trace (or in time mode, which
            // is inherently activity). Then everything — elevation, distance, position, overlays,
            // stop bands — lives on the activity's own scale, so there's no planned-vs-activity
            // offset and a pinned point's map location / profile altitude / distance all agree.
            const useActivity = ts && ts.n && (this.xAxisMode === 'time' || this.dataSource === 'strava');
            if (useActivity) {
                const timeX = this.xAxisMode === 'time';
                activeProfile = {
                    mode: this.xAxisMode, source: 'activity', n: ts.n,
                    x: timeX ? ts.t_s : ts.dist_km, ele: ts.ele_m, slope: ts.slope_pct,
                    lat: ts.lat, lng: ts.lng, distKm: ts.dist_km, timeS: ts.t_s,
                    pace: ts.pace_min_per_km, hr: ts.hr_bpm, cad: ts.cadence_spm,
                };
            } else {
                const t = currentTrack;
                activeProfile = {
                    mode: 'distance', source: 'planned', n: t.n,
                    x: t.dist_km, ele: t.ele_m, slope: t.slope_pct,
                    lat: t.lat, lng: t.lng, distKm: t.dist_km, timeS: cumulativeTimeS,
                    pace: this.comparison?.pace_min_per_km, hr: this.comparison?.hr_bpm, cad: this.comparison?.cadence_spm,
                };
            }
        },

        updateOverlayData() {
            if (!elevationChart) return;
            if (!this.comparison) this.xAxisMode = 'distance';
            elevationChart.$stopPositions = buildStopPositions(this.comparison?.stops);
            this.setXAxisMode(this.xAxisMode);
        },

        setXAxisMode(mode) {
            if (!elevationChart || !currentTrack) return;
            // capture the pinned point's geographic position before the profile (and its index
            // space) changes, so we can re-anchor it by location rather than by distance number
            const pinnedGeo = (pinnedTrackIdx !== null && activeProfile && activeProfile.lat?.[pinnedTrackIdx] != null)
                ? { lat: activeProfile.lat[pinnedTrackIdx], lng: activeProfile.lng[pinnedTrackIdx] } : null;
            const useTime = mode === 'time' && !!this.comparison?.time_series?.n;
            this.xAxisMode = useTime ? 'time' : 'distance';
            this.rebuildActiveProfile();
            elevationChart.data.datasets[0].data = xyData(activeProfile.ele);
            elevationChart.data.datasets[2].data = activeProfile.pace ? xyData(activeProfile.pace) : [];
            elevationChart.data.datasets[3].data = activeProfile.hr ? xyData(activeProfile.hr) : [];
            elevationChart.data.datasets[4].data = activeProfile.cad ? xyData(activeProfile.cad) : [];
            elevationChart.$xAxisMode = this.xAxisMode;
            const xAxis = elevationChart.options.scales.x;
            xAxis.max = activeProfile.x[activeProfile.n - 1];
            xAxis.title.text = useTime ? 'Time' : 'Distance (km)';
            xAxis.ticks = useTime
                ? { callback: (val) => this.formatSplitTime(Math.round(val)) }
                : {};
            this.updateMarkerOverlayDataset();
            // the pinned index lives in the previous mode's index space; re-anchor it by geography
            // (location is stable across modes; the distance number is not, planned vs activity)
            if (pinnedTrackIdx !== null && activeProfile.ele) {
                pinnedTrackIdx = pinnedGeo
                    ? nearestProfileIndex(pinnedGeo.lat, pinnedGeo.lng)
                    : profileIndexForDistance(this.pinnedDistanceKm);
                showPinnedAtIndex(pinnedTrackIdx);
                elevationChart.data.datasets[5].data = [{ x: activeProfile.x[pinnedTrackIdx], y: activeProfile.ele[pinnedTrackIdx] }];
            }
            this.updateOverlayVisibility();
        },

        updateOverlayVisibility() {
            if (!elevationChart) return;
            const showPace = !!(this.comparison && this.showPaceOverlay);
            const showHr = !!(this.comparison && this.showHrOverlay);
            const showCadence = !!(this.comparison && this.showCadenceOverlay);
            elevationChart.data.datasets[2].hidden = !showPace;
            elevationChart.data.datasets[3].hidden = !showHr;
            elevationChart.data.datasets[4].hidden = !showCadence;
            elevationChart.options.scales.pace.display = showPace;
            elevationChart.options.scales.hr.display = showHr;
            elevationChart.options.scales.cadence.display = showCadence;
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

        async copyCoachText() {
            if (!this.route) return;
            try {
                const res = await fetch(`${API_BASE}/routes/${this.route.id}/coach-text`);
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || 'Failed to load coach text');
                await navigator.clipboard.writeText(data?.text || '');
                this.coachTextCopied = true;
                setTimeout(() => { this.coachTextCopied = false; }, 1500);
            } catch (e) {
                this.chatError = e?.message || 'Failed to copy coach text.';
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
