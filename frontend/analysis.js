const API_BASE = 'http://localhost:8000/api';

let charts = [];
let isSyncingHover = false;
let periodOffset = 0;

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

function labelForWeek(startDate) {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(startDate);
}

function formatPeriodSummary(startDate, endDate) {
  const fmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });
  return `${fmt.format(startDate)} → ${fmt.format(endDate)}`;
}

function formatAxisTick(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '';
  const rounded = Math.round(num);
  return rounded.toLocaleString('en-US');
}

function niceCeil(value) {
  const n = Math.max(1, Number(value) || 1);
  const exp = Math.floor(Math.log10(n));
  const base = Math.pow(10, exp);
  const norm = n / base;
  const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return step * base;
}

function suggestedMaxFromData(values, { padRatio = 0.12 } = {}) {
  const valid = (values || []).filter((v) => Number.isFinite(v));
  if (valid.length === 0) return 10;
  const maxValue = Math.max(...valid);
  return niceCeil(maxValue * (1 + padRatio));
}

function computeBounds(values, { padRatio = 0.12, minSpan = 1 } = {}) {
  const valid = (values || []).filter((v) => Number.isFinite(v));
  if (valid.length === 0) return { min: 0, max: minSpan };
  const minValue = Math.min(...valid);
  const maxValue = Math.max(...valid);
  const span = Math.max(minSpan, maxValue - minValue);
  const pad = span * padRatio;
  return {
    min: minValue - pad,
    max: maxValue + pad,
  };
}

async function fetchWeekSummary(year, week) {
  const res = await fetch(`${API_BASE}/summary/week/${year}/${week}`);
  if (!res.ok) return null;
  return await res.json();
}

async function fetchTrainingLoad(startDate, endDate) {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  const res = await fetch(`${API_BASE}/training-load?${params.toString()}`);
  if (!res.ok) return null;
  return await res.json();
}

function computeMetrics(summary) {
  const sessions = summary?.sessions || [];
  const runTrailKm = sessions
    .filter((session) => ['run', 'trail'].includes(session.type))
    .reduce((sum, session) => sum + (session.distance_km || 0), 0);
  const elevationM = sessions
    .filter((session) => ['run', 'trail', 'hike'].includes(session.type))
    .reduce((sum, session) => sum + (session.elevation_gain_m || 0), 0);
  const bikeKm = sessions
    .filter((session) => session.type === 'bike')
    .reduce((sum, session) => sum + (session.distance_km || 0), 0);
  const swimKm = sessions
    .filter((session) => session.type === 'swim')
    .reduce((sum, session) => sum + (session.distance_km || 0), 0);
  const strengthMinutes = sessions
    .filter((session) => session.type === 'strength')
    .reduce((sum, session) => sum + (session.duration_minutes || 0), 0);
  const totalHours = sessions
    .reduce((sum, session) => sum + (session.duration_minutes || 0), 0) / 60;

  return {
    runTrailKm: Math.round(runTrailKm * 10) / 10,
    elevationM: Math.round(elevationM),
    bikeKm: Math.round(bikeKm * 10) / 10,
    swimKm: Math.round(swimKm * 10) / 10,
    strengthHours: Math.round((strengthMinutes / 60) * 10) / 10,
    totalHours: Math.round(totalHours * 10) / 10,
  };
}

function registerChart(chart) {
  charts.push(chart);
  return chart;
}

function clearCharts() {
  charts.forEach((chart) => chart.destroy());
  charts = [];
}

function getActiveElementsForIndex(chart, index) {
  if (index === null || index === undefined) return [];
  const active = [];
  chart.data.datasets.forEach((dataset, datasetIndex) => {
    const value = dataset?.data?.[index];
    if (value !== null && value !== undefined) {
      active.push({ datasetIndex, index });
    }
  });
  return active;
}

function syncChartsAtIndex(sourceChart, index) {
  if (isSyncingHover) return;
  isSyncingHover = true;
  charts.forEach((chart) => {
    if (chart === sourceChart) return;
    const active = getActiveElementsForIndex(chart, index);
    chart.setActiveElements(active);
    chart.tooltip.setActiveElements(active, { x: 0, y: 0 });
    chart.update('none');
  });
  isSyncingHover = false;
}

function baseOptions({ yTickFormatter, tooltipFilter, pointStyle = 'circle', showLegend = true, yBounds = null, beginAtZero = true, yMax = null } = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index',
      intersect: false,
      axis: 'x',
    },
    onHover(_event, elements, chart) {
      const index = elements && elements.length > 0 ? elements[0].index : null;
      syncChartsAtIndex(chart, index);
    },
    plugins: {
      legend: {
        display: showLegend,
        position: 'right',
        labels: {
          boxWidth: 10,
          boxHeight: 10,
          usePointStyle: true,
          pointStyle,
          filter: (item, data) => {
            const dataset = data.datasets[item.datasetIndex];
            return dataset.showInLegend !== false;
          },
        },
      },
      tooltip: {
        intersect: false,
        mode: 'index',
        filter: tooltipFilter,
      },
    },
    scales: {
      x: {
        ticks: {
          maxRotation: 0,
          minRotation: 0,
        },
      },
      y: {
        beginAtZero,
        min: yBounds?.min,
        max: yMax ?? yBounds?.max,
        ticks: { callback: yTickFormatter || formatAxisTick },
        afterFit(scale) {
          scale.width = 62;
        },
      },
    },
  };
}

function getWeeklyTrainingLoadPoints(weeks, trainingLoadResponse) {
  const daily = trainingLoadResponse?.daily || [];
  const byDate = new Map(daily.map((point) => [String(point.date), point]));

  return weeks.map((week) => {
    const endDate = addDays(week.start, 6);
    const endIso = toIsoDate(endDate);
    const exact = byDate.get(endIso);
    if (exact) return exact;

    for (let i = 0; i < 7; i += 1) {
      const fallback = byDate.get(toIsoDate(addDays(endDate, -i)));
      if (fallback) return fallback;
    }
    return null;
  });
}

function makeShapeTrendChart(labels, weeklyLoadPoints) {
  const ctx = document.getElementById('chart-shape-trend').getContext('2d');

  const shapeValues = weeklyLoadPoints.map((point) => (point ? Math.round(Number(point.ctl || 0)) : null));
  const trendPct = weeklyLoadPoints.map((point) => {
    if (!point || point.acwr === null || point.acwr === undefined) return null;
    return Math.round(Number(point.acwr) * 100);
  });

  const shapeBounds = computeBounds(shapeValues, { padRatio: 0.15, minSpan: 8 });
  const trendBoundsRaw = computeBounds([...trendPct, 100, 150], { padRatio: 0.1, minSpan: 20 });
  const trendBounds = {
    min: Math.max(0, trendBoundsRaw.min),
    max: trendBoundsRaw.max,
  };

  return registerChart(new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '🔋',
          data: shapeValues,
          borderColor: '#3b82f6',
          backgroundColor: '#3b82f6',
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
          yAxisID: 'y',
        },
        {
          label: 'zone-high',
          data: labels.map(() => 150),
          borderWidth: 0,
          pointRadius: 0,
          yAxisID: 'yPct',
          showInLegend: false,
        },
        {
          label: 'zone-low',
          data: labels.map(() => 100),
          borderWidth: 0,
          pointRadius: 0,
          yAxisID: 'yPct',
          showInLegend: false,
          fill: '-1',
          backgroundColor: 'rgba(124,58,237,0.10)',
        },
        {
          label: '📈',
          data: trendPct,
          borderColor: '#7c3aed',
          backgroundColor: '#7c3aed',
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
          yAxisID: 'yPct',
        },
      ],
    },
    options: {
      ...baseOptions({ pointStyle: 'line', showLegend: false }),
      scales: {
        x: { ticks: { maxRotation: 0, minRotation: 0 } },
        y: {
          beginAtZero: false,
          min: shapeBounds.min,
          max: shapeBounds.max,
          ticks: { callback: formatAxisTick },
          afterFit(scale) {
            scale.width = 62;
          },
        },
        yPct: {
          beginAtZero: false,
          min: trendBounds.min,
          max: trendBounds.max,
          position: 'right',
          grid: { drawOnChartArea: false },
          ticks: { callback: (v) => `${Math.round(v)}%` },
          afterFit(scale) {
            scale.width = 62;
          },
        },
      },
      plugins: {
        ...baseOptions({ pointStyle: 'line', showLegend: false }).plugins,
        tooltip: {
          intersect: false,
          mode: 'index',
          filter: (ctx) => ctx.datasetIndex === 0 || ctx.datasetIndex === 3,
        },
      },
    },
  }));
}

function makeMetricChart(canvasId, labels, data, label, color, fitScale = false) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  const yBounds = fitScale ? computeBounds(data, { padRatio: 0.15, minSpan: 1 }) : null;
  const yMax = !fitScale ? suggestedMaxFromData(data) : null;

  return registerChart(new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label,
          data,
          borderColor: color,
          backgroundColor: color,
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
        },
      ],
    },
    options: baseOptions({ yBounds, beginAtZero: !fitScale, yMax }),
  }));
}

function buildWeeksFromEnd(endDate, weeksCount) {
  const endWeekStart = getStartOfIsoWeek(endDate);
  const weeks = [];

  for (let i = weeksCount - 1; i >= 0; i -= 1) {
    const weekStart = addDays(endWeekStart, -7 * i);
    weeks.push({
      start: weekStart,
      year: getIsoWeekYear(weekStart),
      week: getIsoWeek(weekStart),
      label: labelForWeek(weekStart),
    });
  }

  return weeks;
}

function getPeriodRange() {
  const endInput = document.getElementById('end-date');
  const lookbackInput = document.getElementById('lookback-weeks');

  const baseEnd = endInput.value ? new Date(`${endInput.value}T12:00:00`) : new Date();
  const weeksCount = Math.max(1, Number(lookbackInput.value || 12));

  const alignedEndWeekStart = getStartOfIsoWeek(baseEnd);
  const shiftedEndWeekStart = addDays(alignedEndWeekStart, -(periodOffset * weeksCount * 7));
  const start = addDays(shiftedEndWeekStart, -7 * (weeksCount - 1));
  const end = addDays(shiftedEndWeekStart, 6);

  return { start, end, weeksCount };
}

async function refreshTrends() {
  const { start, end, weeksCount } = getPeriodRange();
  const weeks = buildWeeksFromEnd(end, weeksCount);

  const summaries = await Promise.all(weeks.map((week) => fetchWeekSummary(week.year, week.week)));
  const metrics = summaries.map((summary) => computeMetrics(summary));

  const trainingLoad = await fetchTrainingLoad(toIsoDate(start), toIsoDate(end));
  const weeklyLoadPoints = getWeeklyTrainingLoadPoints(weeks, trainingLoad);
  const labels = weeks.map((week) => week.label);

  clearCharts();
  makeShapeTrendChart(labels, weeklyLoadPoints);
  makeMetricChart('chart-run', labels, metrics.map((m) => m.runTrailKm), '🏃', '#ef4444', false);
  makeMetricChart('chart-elev', labels, metrics.map((m) => m.elevationM), '⛰️', '#16a34a');
  makeMetricChart('chart-bike', labels, metrics.map((m) => m.bikeKm), '🚴', '#f97316');
  makeMetricChart('chart-swim', labels, metrics.map((m) => m.swimKm), '🏊', '#3b82f6');
  makeMetricChart('chart-strength', labels, metrics.map((m) => m.strengthHours), '💪', '#7c3aed');
  makeMetricChart('chart-total', labels, metrics.map((m) => m.totalHours), '⏱️', '#111827');

  const summaryEl = document.getElementById('summary');
  summaryEl.textContent = formatPeriodSummary(start, end);
}

function initControls() {
  const endInput = document.getElementById('end-date');
  const refreshBtn = document.getElementById('refresh-btn');
  const lookbackInput = document.getElementById('lookback-weeks');
  const prevBtn = document.getElementById('prev-period');
  const nextBtn = document.getElementById('next-period');

  endInput.value = toIsoDate(new Date());

  refreshBtn.addEventListener('click', () => {
    periodOffset = 0;
    refreshTrends();
  });

  lookbackInput.addEventListener('change', () => {
    periodOffset = 0;
    refreshTrends();
  });

  endInput.addEventListener('change', () => {
    periodOffset = 0;
    refreshTrends();
  });

  prevBtn.addEventListener('click', () => {
    periodOffset += 1;
    refreshTrends();
  });

  nextBtn.addEventListener('click', () => {
    periodOffset = Math.max(0, periodOffset - 1);
    refreshTrends();
  });
}

initControls();
refreshTrends();
