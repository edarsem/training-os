const API_BASE = 'http://localhost:8000/api';

let charts = [];
let isSyncingHover = false;

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

function clearCharts() {
  charts.forEach((chart) => chart.destroy());
  charts = [];
}

function renderCharts(daily) {
  clearCharts();

  const labels = daily.map((point) => formatDayLabel(String(point.date)));

  const dailyLoadChart = new Chart(document.getElementById('chart-daily-load').getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: '⚡ Daily load',
          data: daily.map((point) => Number(point.load || 0)),
          backgroundColor: '#0ea5e9',
          borderRadius: 3,
        },
      ],
    },
    options: baseOptions(),
  });
  registerChart(dailyLoadChart);

  const trendStressChart = new Chart(document.getElementById('chart-trend-stress').getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '🔥 Stress',
          data: daily.map((point) => Number(point.atl || 0)),
          borderColor: '#f97316',
          backgroundColor: '#f97316',
          tension: 0.2,
          borderWidth: 2,
          pointRadius: 2,
        },
        {
          label: '📈 Trend',
          data: daily.map((point) => Number(point.ctl || 0)),
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
  registerChart(trendStressChart);

  const thresholds = [50, 80, 100, 150];
  const shapeChart = new Chart(document.getElementById('chart-shape-daily').getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '⚖️ Shape',
          data: daily.map((point) => {
            if (point.acwr === null || point.acwr === undefined) return null;
            return Math.round(Number(point.acwr) * 1000) / 10;
          }),
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
  registerChart(shapeChart);
}

function updateSummary(daily) {
  const summary = document.getElementById('summary');
  if (!daily || daily.length === 0) {
    summary.textContent = 'No data in selected range';
    return;
  }
  const last = daily[daily.length - 1];
  const trend = Math.round(Number(last.ctl || 0));
  const stress = Math.round(Number(last.atl || 0));
  const shape = last.acwr === null || last.acwr === undefined ? '—' : `${Math.round(Number(last.acwr) * 1000) / 10}%`;
  summary.textContent = `Latest: 📈 Trend ${trend} · 🔥 Stress ${stress} · ⚖️ Shape ${shape}`;
}

async function refresh() {
  const endInput = document.getElementById('end-date');
  const lookbackInput = document.getElementById('lookback-days');

  const endDate = endInput.value ? new Date(`${endInput.value}T12:00:00`) : new Date();
  const lookbackDays = Math.max(1, Number(lookbackInput.value || 14));
  const startDate = addDays(endDate, -(lookbackDays - 1));

  const startIso = toIsoDate(startDate);
  const endIso = toIsoDate(endDate);

  const response = await fetchTrainingLoad(startIso, endIso);
  const daily = response?.daily || [];

  renderCharts(daily);
  updateSummary(daily);
}

function initControls() {
  const todayIso = toIsoDate(new Date());
  const endInput = document.getElementById('end-date');
  const refreshBtn = document.getElementById('refresh-btn');
  const lookbackInput = document.getElementById('lookback-days');

  endInput.value = todayIso;
  refreshBtn.addEventListener('click', () => refresh());
  lookbackInput.addEventListener('change', () => refresh());
  endInput.addEventListener('change', () => refresh());
}

initControls();
refresh();
