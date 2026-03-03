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

function formatDayLabel(isoDate) {
  const d = new Date(`${isoDate}T12:00:00`);
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(d);
}

function formatPeriodSummary(startDate, endDate) {
  const fmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });
  return `${fmt.format(startDate)} → ${fmt.format(endDate)}`;
}

async function fetchTrainingLoad(startDate, endDate) {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  const res = await fetch(`${API_BASE}/training-load?${params.toString()}`);
  if (!res.ok) return null;
  return await res.json();
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

function baseOptions({ yTickFormatter, tooltipFilter, pointStyle = 'circle', beginAtZero = true } = {}) {
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
        ticks: yTickFormatter
          ? {
              callback: yTickFormatter,
            }
          : undefined,
      },
    },
  };
}

function makeStressShapeChart(labels, daily) {
  const canvas = document.getElementById('chart-stress-shape');
  if (!canvas) return;

  registerChart(new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '🔥',
          data: daily.map((point) => Math.round(Number(point.atl || 0))),
          borderColor: '#f97316',
          backgroundColor: '#f97316',
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
        },
        {
          label: '🔋',
          data: daily.map((point) => Math.round(Number(point.ctl || 0))),
          borderColor: '#3b82f6',
          backgroundColor: '#3b82f6',
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
        },
      ],
    },
    options: baseOptions({ beginAtZero: false }),
  }));
}

function makeTrendChart(labels, daily) {
  const canvas = document.getElementById('chart-trend');
  if (!canvas) return;

  const thresholds = [50, 80, 100, 150];

  registerChart(new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'zone-high',
          data: labels.map(() => 150),
          borderWidth: 0,
          pointRadius: 0,
          showInLegend: false,
        },
        {
          label: 'zone-low',
          data: labels.map(() => 100),
          borderWidth: 0,
          pointRadius: 0,
          showInLegend: false,
          fill: '-1',
          backgroundColor: 'rgba(124,58,237,0.10)',
        },
        {
          label: '📈',
          data: daily.map((point) => {
            if (point.acwr === null || point.acwr === undefined) return null;
            return Math.round(Number(point.acwr) * 100);
          }),
          borderColor: '#7c3aed',
          backgroundColor: '#7c3aed',
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
        },
        ...thresholds.map((threshold) => ({
          label: '•',
          data: labels.map(() => threshold),
          borderColor: '#9ca3af',
          backgroundColor: '#9ca3af',
          borderDash: [5, 4],
          borderWidth: 1,
          pointRadius: 0,
          showInLegend: false,
        })),
      ],
    },
    options: baseOptions({
      yTickFormatter: (value) => `${Math.round(Number(value) || 0)}%`,
      tooltipFilter: (ctx) => ctx.datasetIndex === 2,
      pointStyle: 'line',
      beginAtZero: false,
    }),
  }));
}

function makeDailyLoadChart(labels, daily) {
  const canvas = document.getElementById('chart-daily-load');
  if (!canvas) return;

  registerChart(new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: '⚡',
          data: daily.map((point) => Math.round(Number(point.load || 0))),
          backgroundColor: '#0ea5e9',
          borderRadius: 3,
        },
      ],
    },
    options: baseOptions(),
  }));
}

function updateSummary(daily, startDate, endDate) {
  const summary = document.getElementById('summary');
  if (!summary) return;
  if (!daily || daily.length === 0) {
    summary.textContent = `${formatPeriodSummary(startDate, endDate)} · No data`;
    return;
  }

  const last = daily[daily.length - 1];
  const stress = Math.round(Number(last.atl || 0));
  const shape = Math.round(Number(last.ctl || 0));
  const trend = last.acwr === null || last.acwr === undefined ? '—' : `${Math.round(Number(last.acwr) * 100)}%`;
  summary.textContent = `${formatPeriodSummary(startDate, endDate)} · 🔥 ${stress} · 🔋 ${shape} · 📈 ${trend}`;
}

function getPeriodRange() {
  const endInput = document.getElementById('end-date');
  const lookbackInput = document.getElementById('lookback-days');

  const baseEnd = endInput.value ? new Date(`${endInput.value}T12:00:00`) : new Date();
  const lookbackDays = Math.max(1, Number(lookbackInput.value || 28));
  const shiftedEnd = addDays(baseEnd, -(periodOffset * lookbackDays));
  const start = addDays(shiftedEnd, -(lookbackDays - 1));

  return { start, end: shiftedEnd, lookbackDays };
}

async function refresh() {
  const { start, end } = getPeriodRange();
  const startIso = toIsoDate(start);
  const endIso = toIsoDate(end);

  const response = await fetchTrainingLoad(startIso, endIso);
  const daily = response?.daily || [];
  const labels = daily.map((point) => formatDayLabel(String(point.date)));

  clearCharts();
  makeStressShapeChart(labels, daily);
  makeTrendChart(labels, daily);
  makeDailyLoadChart(labels, daily);
  updateSummary(daily, start, end);
}

function initControls() {
  const endInput = document.getElementById('end-date');
  const refreshBtn = document.getElementById('refresh-btn');
  const lookbackInput = document.getElementById('lookback-days');
  const prevBtn = document.getElementById('prev-period');
  const nextBtn = document.getElementById('next-period');

  endInput.value = toIsoDate(new Date());

  refreshBtn.addEventListener('click', () => {
    periodOffset = 0;
    refresh();
  });
  lookbackInput.addEventListener('change', () => {
    periodOffset = 0;
    refresh();
  });
  endInput.addEventListener('change', () => {
    periodOffset = 0;
    refresh();
  });

  prevBtn.addEventListener('click', () => {
    periodOffset += 1;
    refresh();
  });

  nextBtn.addEventListener('click', () => {
    periodOffset = Math.max(0, periodOffset - 1);
    refresh();
  });
}

initControls();
refresh();
