// charts.js — Chart.js wrappers styled to match the original recharts look.
const PALETTE = {
  border: "#28313D",
  textMuted: "#8D98A6",
  textPrimary: "#EAEDF1",
  surfaceRaised: "#1D2530",
  teal: "#3FBFA6",
  amber: "#E3A83B",
  coral: "#E2604F",
  blue: "#5B8DEF",
};

const Charts = {
  _tooltipBase: {
    backgroundColor: PALETTE.surfaceRaised,
    borderColor: PALETTE.border,
    borderWidth: 1,
    titleColor: PALETTE.textPrimary,
    bodyColor: PALETTE.textPrimary,
    padding: 10,
    displayColors: false,
    titleFont: { family: "Manrope", size: 12 },
    bodyFont: { family: "Manrope", size: 12 },
  },

  /** Horizontal bar chart: assessment-by-track / module scores. */
  horizontalBar(canvas, rows, labelKey, valueKey, color, unitSuffix = "") {
    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: rows.map((r) => r[labelKey]),
        datasets: [{ data: rows.map((r) => r[valueKey]), backgroundColor: color, borderRadius: 4, maxBarThickness: 16 }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            min: 0, max: 100,
            grid: { color: PALETTE.border, drawTicks: false },
            ticks: { color: PALETTE.textMuted, font: { family: "JetBrains Mono", size: 11 } },
            border: { color: PALETTE.border },
          },
          y: {
            grid: { display: false },
            ticks: { color: PALETTE.textMuted, font: { family: "Manrope", size: 12 } },
            border: { color: PALETTE.border },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: { ...Charts._tooltipBase, callbacks: { label: (ctx) => `Assessment score: ${ctx.raw}${unitSuffix}` } },
        },
      },
    });
  },

  /** Vertical bar chart: assessment score by track. */
  verticalBar(canvas, rows, labelKey, valueKey, color, unitSuffix = "") {
    const splitLabel = (str) => {
      if (!str || str.length <= 10) return str;
      const words = str.split(" ");
      if (words.length <= 1) return str;
      const mid = Math.ceil(words.length / 2);
      return [words.slice(0, mid).join(" "), words.slice(mid).join(" ")];
    };

    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: rows.map((r) => splitLabel(r[labelKey])),
        datasets: [{
          data: rows.map((r) => {
            const val = r[valueKey];
            if (val === null || val === undefined || val === "") return 0;
            const num = Number(val);
            return isNaN(num) ? 0 : num;
          }),
          backgroundColor: color,
          borderRadius: 4,
          maxBarThickness: 28,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: PALETTE.textMuted,
              font: { family: "Manrope", size: 10, weight: "500" },
              minRotation: 45,
              maxRotation: 45,
              autoSkip: false,
            },
            border: { color: PALETTE.border },
          },
          y: {
            min: 0,
            max: 100,
            grid: { color: PALETTE.border, drawTicks: false },
            ticks: {
              color: PALETTE.textMuted,
              font: { family: "JetBrains Mono", size: 11 },
              stepSize: 20,
            },
            border: { color: PALETTE.border },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...Charts._tooltipBase,
            callbacks: {
              title: (items) => {
                if (!items.length) return "";
                const raw = items[0].label;
                return Array.isArray(raw) ? raw.join(" ") : raw;
              },
              label: (ctx) => `Assessment score: ${ctx.raw}${unitSuffix}`,
            },
          },
        },
      },
    });
  },

  /** Donut chart: warning panel. */
  donut(canvas, segments) {
    return new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: segments.map((s) => s.name),
        datasets: [{ data: segments.map((s) => s.value), backgroundColor: segments.map((s) => s.color), borderWidth: 0 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "64%",
        animation: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
    });
  },

  /** Stacked bar chart: attendance present/absent. */
  stackedBar(canvas, rows) {
    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: rows.map((r) => r.label),
        datasets: [
          { label: "Present", data: rows.map((r) => r.present), backgroundColor: PALETTE.teal, stack: "a", maxBarThickness: 22 },
          { label: "Absent", data: rows.map((r) => r.absent), backgroundColor: PALETTE.surfaceRaised, stack: "a", maxBarThickness: 22, borderRadius: { topLeft: 3, topRight: 3 } },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { color: PALETTE.textMuted, font: { family: "Manrope", size: 11 } }, border: { color: PALETTE.border } },
          y: { stacked: true, grid: { color: PALETTE.border }, ticks: { color: PALETTE.textMuted, font: { family: "JetBrains Mono", size: 11 } }, border: { color: PALETTE.border } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { ...Charts._tooltipBase },
        },
      },
    });
  },

  /** Line chart: score trend over time. */
  line(canvas, rows, labelKey, valueKey, color, unitSuffix = "") {
    return new Chart(canvas, {
      type: "line",
      data: {
        labels: rows.map((r) => r[labelKey]),
        datasets: [{
          data: rows.map((r) => r[valueKey]),
          borderColor: color,
          backgroundColor: color,
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: color,
          pointBorderWidth: 0,
          tension: 0.35,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        scales: {
          x: { grid: { display: false }, ticks: { color: PALETTE.textMuted, font: { family: "Manrope", size: 11 } }, border: { color: PALETTE.border } },
          y: { min: 0, max: 100, grid: { color: PALETTE.border }, ticks: { color: PALETTE.textMuted, font: { family: "JetBrains Mono", size: 11 } }, border: { color: PALETTE.border } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { ...Charts._tooltipBase, callbacks: { label: (ctx) => `Score: ${ctx.raw}${unitSuffix}` } },
        },
      },
    });
  },

  destroy(chart) {
    if (chart) chart.destroy();
  },
};
