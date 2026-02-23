const API_BASE = 'http://localhost:8000/api';

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

async function fetchWeekSummary(year, week) {
  const res = await fetch(`${API_BASE}/summary/week/${year}/${week}`);
  if (!res.ok) {
    return null;
  }
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

function makeChart(canvasId, labels, data, title, color) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  new Chart(ctx, {
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
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'right',
          labels: {
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
            pointStyle: 'circle'
          }
        },
      },
      scales: {
        x: {
          ticks: {
            maxRotation: 0,
            minRotation: 0,
          }
        },
        y: { beginAtZero: true },
      },
    },
  });
}

async function buildChart() {
  const now = new Date();
  const currentWeekStart = getStartOfIsoWeek(now);

  const weeks = [];
  for (let i = 19; i >= 0; i -= 1) {
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

  const labels = weeks.map((week) => week.label);

  makeChart('chart-run', labels, metrics.map((m) => m.runTrailKm), '🏃', '#ef4444');
  makeChart('chart-elev', labels, metrics.map((m) => m.elevationM), '⛰️', '#16a34a');
  makeChart('chart-bike', labels, metrics.map((m) => m.bikeKm), '🚴', '#f97316');
  makeChart('chart-swim', labels, metrics.map((m) => m.swimKm), '🏊', '#3b82f6');
  makeChart('chart-strength', labels, metrics.map((m) => m.strengthHours), '💪', '#7c3aed');
  makeChart('chart-total', labels, metrics.map((m) => m.totalHours), '⏱️', '#111827');
}

buildChart();
