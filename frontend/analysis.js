const API_BASE = 'http://localhost:8000/api';
const ANALYSIS_WEEKS = 20;
const chartRegistry = [];
let isSyncingHover = false;

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
  const d = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(startDate);
  return d;
}

function registerChart(chart) {
  chartRegistry.push(chart);
  return chart;
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
  chartRegistry.forEach((chart) => {
    if (chart === sourceChart) return;
    const active = getActiveElementsForIndex(chart, index);
    chart.setActiveElements(active);
    chart.tooltip.setActiveElements(active, { x: 0, y: 0 });
    chart.update('none');
  });
  isSyncingHover = false;
}

function baseOptions({ yTickFormatter, tooltipFilter, pointStyle = 'circle' } = {}) {
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
        display: true,
        position: 'right',
        labels: {
          boxWidth: 10,
          boxHeight: 10,
          usePointStyle: true,
          pointStyle,
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
        beginAtZero: true,
        ticks: yTickFormatter
          ? {
              callback: yTickFormatter,
            }
          : undefined,
      },
    },
  };
}

async function fetchWeekSummary(year, week) {
  const res = await fetch(`${API_BASE}/summary/week/${year}/${week}`);
  if (!res.ok) {
    return null;
  }
  return await res.json();
}

async function fetchTrainingLoad(startDate, endDate) {
  const params = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
  });
  const res = await fetch(`${API_BASE}/training-load?${params.toString()}`);
  if (!res.ok) {
    return null;
  }
  return await res.json();
}

function toIsoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
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

function makeChart(canvasId, labels, data, title, color) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: title,
          data,
          borderColor: color,
          backgroundColor: color,
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
        },
      ],
    },
    options: baseOptions(),
  });

  return registerChart(chart);
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

function makeAtlCtlChart(labels, weeklyLoadPoints) {
  const ctx = document.getElementById('chart-atl-ctl').getContext('2d');
  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '🔥 Stress',
          data: weeklyLoadPoints.map((point) => (point ? point.atl : null)),
          borderColor: '#f97316',
          backgroundColor: '#f97316',
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
        },
        {
          label: '📈 Trend',
          data: weeklyLoadPoints.map((point) => (point ? point.ctl : null)),
          borderColor: '#3b82f6',
          backgroundColor: '#3b82f6',
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
        },
      ],
    },
    options: baseOptions(),
  });

  return registerChart(chart);
}

function makeShapeChart(labels, weeklyLoadPoints) {
  const ctx = document.getElementById('chart-shape').getContext('2d');
  const acwrPctValues = weeklyLoadPoints.map((point) => {
    if (!point || point.acwr === null || point.acwr === undefined) return null;
    return Math.round(Number(point.acwr) * 1000) / 10;
  });
  const thresholds = [50, 80, 100, 150];

  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '⚖️ Shape',
          data: acwrPctValues,
          borderColor: '#7c3aed',
          backgroundColor: '#7c3aed',
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
        },
        ...thresholds.map((threshold) => ({
          label: `Guide ${threshold}%`,
          data: labels.map(() => threshold),
          borderColor: '#9ca3af',
          backgroundColor: '#9ca3af',
          borderDash: [5, 4],
          borderWidth: 1,
          pointRadius: 0,
        })),
      ],
    },
    options: baseOptions({
      yTickFormatter: (value) => `${value}%`,
      tooltipFilter: (ctx) => ctx.datasetIndex === 0,
      pointStyle: 'line',
    }),
  });

  return registerChart(chart);
}

async function buildChart() {
  const now = new Date();
  const currentWeekStart = getStartOfIsoWeek(now);

  const weeks = [];
  for (let i = ANALYSIS_WEEKS - 1; i >= 0; i -= 1) {
    const weekStart = addDays(currentWeekStart, -7 * i);
    weeks.push({
      start: weekStart,
      year: getIsoWeekYear(weekStart),
      week: getIsoWeek(weekStart),
      label: labelForWeek(weekStart),
    });
  }

  const summaries = await Promise.all(weeks.map((week) => fetchWeekSummary(week.year, week.week)));
  const metrics = summaries.map((summary) => computeMetrics(summary));
  const analysisStart = toIsoDate(weeks[0].start);
  const analysisEnd = toIsoDate(addDays(weeks[weeks.length - 1].start, 6));
  const trainingLoad = await fetchTrainingLoad(analysisStart, analysisEnd);
  const weeklyLoadPoints = getWeeklyTrainingLoadPoints(weeks, trainingLoad);

  const labels = weeks.map((week) => week.label);

  makeAtlCtlChart(labels, weeklyLoadPoints);
  makeShapeChart(labels, weeklyLoadPoints);
  makeChart('chart-run', labels, metrics.map((m) => m.runTrailKm), '🏃', '#ef4444');
  makeChart('chart-elev', labels, metrics.map((m) => m.elevationM), '⛰️', '#16a34a');
  makeChart('chart-bike', labels, metrics.map((m) => m.bikeKm), '🚴', '#f97316');
  makeChart('chart-swim', labels, metrics.map((m) => m.swimKm), '🏊', '#3b82f6');
  makeChart('chart-strength', labels, metrics.map((m) => m.strengthHours), '💪', '#7c3aed');
  makeChart('chart-total', labels, metrics.map((m) => m.totalHours), '⏱️', '#111827');
}

buildChart();
