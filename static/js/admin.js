// admin.js — Admin tab: overview page + its three drill-down modals.
const STUDENT_PANEL_CATEGORIES = {
  selected: { label: "Selected", color: PALETTE.teal },
  rejected: { label: "Rejected", color: PALETTE.coral },
};

const AdminView = {
  charts: [],
  trackFilter: "All tracks",
  activeCategory: "selected",

  async render(content) {
    console.log("[AdminView] rendering Admin page", { trackFilter: AdminView.trackFilter });
    const overview = await Api.get(`/api/admin/overview?track=${encodeURIComponent(AdminView.trackFilter)}`);

    AdminView.charts.forEach(Charts.destroy);
    AdminView.charts = [];

    const frag = UI.clone("tpl-admin-overview");
    const r = UI.refs(frag);
    r.subtitle.textContent = `Cohort health — ${AdminView.trackFilter.toLowerCase()}`;

    UI.buildTrackFilter(r["track-filter"], {
      value: AdminView.trackFilter,
      options: TRACK_FILTER_OPTIONS,
      onChange: (opt) => { AdminView.trackFilter = opt; AdminView.render(content); },
    });

    UI.renderStatGrid(r.stats, overview.stats, {
      strength: () => AdminView.openAttendanceModal(),
      score: () => AdminView.openScoreModal(),
      watchlist: () => AdminView.openWatchlistModal(content),
      risk: () => AdminView.openRiskModal(content),
    });

    UI.clear(content);
    content.appendChild(frag);
    UI.refreshIcons();

    content.querySelector("[data-el='assessment-chart-box']").style.height = "340px";
    AdminView.charts.push(
      Charts.verticalBar(content.querySelector("[data-el='assessment-chart']"), overview.assessment, "track", "pct", PALETTE.teal)
    );

    AdminView.renderWarningPanel(content, overview.warning);
    await AdminView.renderStudentPanel(content);
  },

  renderWarningPanel(content, warning) {
    const total = warning.critical + warning.moderate + warning.onTrack;
    const segments = [
      { name: "Critical", value: warning.critical, color: PALETTE.coral },
      { name: "Moderate", value: warning.moderate, color: PALETTE.amber },
      { name: "On track", value: warning.onTrack, color: PALETTE.teal },
    ];
    content.querySelector("[data-el='warning-total']").textContent = total;
    AdminView.charts.push(Charts.donut(content.querySelector("[data-el='warning-chart']"), segments));

    const legend = content.querySelector("[data-el='warning-legend']");
    UI.clear(legend);
    segments.forEach((s) => {
      const row = document.createElement("div");
      row.className = "legend-row";
      row.innerHTML = `<span class="legend-dot" style="background:${s.color}"></span><span class="legend-name"></span><span class="legend-value"></span>`;
      row.querySelector(".legend-name").textContent = s.name;
      row.querySelector(".legend-value").textContent = s.value;
      legend.appendChild(row);
    });
  },

  async renderStudentPanel(content) {
    const tabsEl = content.querySelector("[data-el='category-tabs']");
    const rowsEl = content.querySelector("[data-el='student-rows']");

    if (!["selected", "rejected"].includes(AdminView.activeCategory)) {
      AdminView.activeCategory = "selected";
    }

    const data = await Api.get(
      `/api/admin/students?track=${encodeURIComponent(AdminView.trackFilter)}&category=${AdminView.activeCategory}`
    );

    UI.clear(tabsEl);
    Object.entries(STUDENT_PANEL_CATEGORIES).forEach(([key, meta]) => {
      const btn = document.createElement("button");
      btn.className = "pill" + (key === AdminView.activeCategory ? " active" : "");
      btn.style.setProperty("--pill-color", meta.color);
      btn.innerHTML = `<span class="pill-dot" style="background:${meta.color}"></span>${meta.label} <span class="muted"></span>`;
      btn.querySelector(".muted").textContent = data.counts ? (data.counts[key] ?? 0) : 0;
      btn.addEventListener("click", () => {
        AdminView.activeCategory = key;
        AdminView.renderStudentPanel(content);
      });
      tabsEl.appendChild(btn);
    });

    UI.clear(rowsEl);

    if (!data.students || data.students.length === 0) {
      const emptyTr = document.createElement("tr");
      const emptyTd = document.createElement("td");
      emptyTd.colSpan = 3;
      emptyTd.className = "empty-table-cell";
      emptyTd.textContent = AdminView.activeCategory === "selected" ? "No selected students" : "No rejected students";
      emptyTr.appendChild(emptyTd);
      rowsEl.appendChild(emptyTr);
      return;
    }

    const sortedStudents = [...(data.students || [])].sort((a, b) => {
      const perfA = a.performance !== undefined && a.performance !== null ? Number(a.performance) : (Number(a.totalWeightage) || 0);
      const perfB = b.performance !== undefined && b.performance !== null ? Number(b.performance) : (Number(b.totalWeightage) || 0);
      return perfB - perfA;
    });

    sortedStudents.forEach((s) => {
      const tr = document.createElement("tr");
      const tdName = document.createElement("td");
      tdName.appendChild(UI.linkCell(s.name, () => AdminView.openStudentDetail(content, s.registerNumber)));
      tr.appendChild(tdName);

      const tdPerf = document.createElement("td");
      tdPerf.className = "mono";
      tdPerf.style.color = (data.meta && data.meta.color) || PALETTE.teal;
      const perfRaw = s.performance !== undefined && s.performance !== null ? s.performance : s.totalWeightage;
      if (perfRaw !== undefined && perfRaw !== null && perfRaw !== "") {
        const perfVal = Number(perfRaw) || 0;
        tdPerf.textContent = `${Number.isInteger(perfVal) ? perfVal : perfVal.toFixed(1)}%`;
      } else {
        tdPerf.textContent = "—";
      }
      tr.appendChild(tdPerf);

      const tdWl = document.createElement("td");
      const wlBtn = document.createElement("button");
      wlBtn.className = "watchlist-mini-btn" + (s.onWatchlist ? " active" : "");
      wlBtn.title = s.onWatchlist ? "Remove from watchlist" : "Add to watchlist";
      wlBtn.innerHTML = `<i data-lucide="eye"></i><span>${s.onWatchlist ? "Watching" : "Watch"}</span>`;
      wlBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        wlBtn.disabled = true;
        try {
          const res = await Api.post("/api/admin/watchlist/toggle", { registerNumber: s.registerNumber });
          s.onWatchlist = res.onWatchlist;
          wlBtn.classList.toggle("active", s.onWatchlist);
          wlBtn.title = s.onWatchlist ? "Remove from watchlist" : "Add to watchlist";
          wlBtn.querySelector("span").textContent = s.onWatchlist ? "Watching" : "Watch";
          // Update stat cards immediately
          const overview = await Api.get(`/api/admin/overview?track=${encodeURIComponent(AdminView.trackFilter)}`);
          const statBox = content.querySelector("[data-el='stats']");
          if (statBox) {
            UI.renderStatGrid(statBox, overview.stats, {
              strength: () => AdminView.openAttendanceModal(),
              score: () => AdminView.openScoreModal(),
              watchlist: () => AdminView.openWatchlistModal(content),
              risk: () => AdminView.openRiskModal(content),
            });
          }
        } catch (err) {
          console.error("Watchlist error", err);
        } finally {
          wlBtn.disabled = false;
        }
      });
      tdWl.appendChild(wlBtn);
      tr.appendChild(tdWl);

      rowsEl.appendChild(tr);
    });
    UI.refreshIcons();
  },

  openStudentDetail(content, registerNumber) {
    if (window.AppRouter) {
      window.AppRouter.navigate("student", registerNumber);
    } else {
      StudentView.renderProfileDetail(content, registerNumber, {
        backLabel: "Back to admin overview",
        onBack: () => AdminView.render(content),
        fetchUrl: `/api/admin/student/${encodeURIComponent(registerNumber)}`,
      });
    }
  },

  async openAttendanceModal() {
    const d = await Api.get(`/api/admin/attendance?track=${encodeURIComponent(AdminView.trackFilter)}`);
    const { body, close } = UI.openModal({
      title: "Class attendance by session",
      subtitle: `${AdminView.trackFilter} · last ${d.sessionCount} sessions`,
    });

    body.innerHTML = `
      <div class="modal-summary">
        <div><div class="modal-summary-value"></div><div class="modal-summary-label">avg. students present</div></div>
        <div><div class="modal-summary-value" style="color:${PALETTE.teal}"></div><div class="modal-summary-label">avg. attendance rate</div></div>
      </div>
      <div class="chart-box chart-box-line"><canvas></canvas></div>
    `;
    body.querySelectorAll(".modal-summary-value")[0].textContent = d.avgPresent;
    body.querySelectorAll(".modal-summary-value")[1].textContent = `${d.avgPct}%`;
    const chart = Charts.stackedBar(body.querySelector("canvas"), d.chartData);

    const table = document.createElement("table");
    table.className = "data-table";
    table.style.marginTop = "16px";
    table.innerHTML = `<thead><tr><th>Date</th><th>Day</th><th>Present</th><th>Total</th><th>Attendance</th></tr></thead><tbody></tbody>`;
    const tbody = table.querySelector("tbody");
    d.table.forEach((row) => {
      const tr = document.createElement("tr");
      const pctColor = row.pct >= 90 ? PALETTE.teal : row.pct >= 75 ? PALETTE.amber : PALETTE.coral;
      tr.innerHTML = `<td>${row.date}</td><td class="muted">${row.day}</td><td class="mono">${row.present}</td><td class="mono muted">${row.total}</td><td class="mono" style="color:${pctColor}">${row.pct}%</td>`;
      tbody.appendChild(tr);
    });
    body.appendChild(table);
  },

  async openScoreModal() {
    const d = await Api.get(`/api/admin/scores?track=${encodeURIComponent(AdminView.trackFilter)}`);
    const trendColor = d.trend >= 0 ? PALETTE.teal : PALETTE.coral;
    const { body } = UI.openModal({
      title: "Average score by day",
      subtitle: `${AdminView.trackFilter} · last ${d.sessionCount} sessions`,
    });

    body.innerHTML = `
      <div class="modal-summary">
        <div><div class="modal-summary-value"></div><div class="modal-summary-label">avg. score across sessions</div></div>
        <div><div class="modal-summary-value" style="color:${trendColor}"></div><div class="modal-summary-label">change since first session</div></div>
      </div>
      <div class="chart-box chart-box-line"><canvas></canvas></div>
    `;
    body.querySelectorAll(".modal-summary-value")[0].textContent = d.overallAvg;
    body.querySelectorAll(".modal-summary-value")[1].textContent = `${d.trend >= 0 ? "+" : ""}${d.trend}`;
    Charts.line(body.querySelector("canvas"), d.chartData, "label", "avgScore", PALETTE.blue);

    const table = document.createElement("table");
    table.className = "data-table";
    table.style.marginTop = "16px";
    table.innerHTML = `<thead><tr><th>Date</th><th>Day</th><th>Average score</th></tr></thead><tbody></tbody>`;
    const tbody = table.querySelector("tbody");
    d.table.forEach((row) => {
      const color = row.avgScore >= 75 ? PALETTE.teal : row.avgScore >= 60 ? PALETTE.amber : PALETTE.coral;
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${row.date}</td><td class="muted">${row.day}</td><td class="mono" style="color:${color}">${row.avgScore}</td>`;
      tbody.appendChild(tr);
    });
    body.appendChild(table);
  },

  async openWatchlistModal(content) {
    const d = await Api.get(`/api/admin/watchlist?track=${encodeURIComponent(AdminView.trackFilter)}`);
    const { body, close } = UI.openModal({
      title: "Students on watchlist",
      subtitle: `${AdminView.trackFilter} · ${d.students.length} students`,
      size: "medium",
    });

    if (!d.students || d.students.length === 0) {
      body.innerHTML = `
        <div style="text-align:center; padding: 40px 20px; color: var(--text-muted);">
          <i data-lucide="eye-off" style="width:36px; height:36px; stroke: var(--amber); margin-bottom:12px; display:inline-block;"></i>
          <p style="font-size:14px; margin-bottom:6px; color:var(--text-primary); font-weight:500;">No students currently on watchlist</p>
          <p style="font-size:12px; max-width:320px; margin:0 auto;">Add students from the Student Roster or their profile page using the "Watch" or "Add to watchlist" button.</p>
        </div>
      `;
      UI.refreshIcons();
      return;
    }

    const table = document.createElement("table");
    table.className = "data-table";
    table.style.marginTop = "18px";
    table.innerHTML = `<thead><tr><th>Register No.</th><th>Student</th><th>Branch</th><th>Track</th><th>Score</th><th>Action</th></tr></thead><tbody></tbody>`;
    const tbody = table.querySelector("tbody");
    d.students.forEach((s) => {
      const tr = document.createElement("tr");
      const tdReg = document.createElement("td");
      tdReg.className = "mono";
      tdReg.style.color = PALETTE.amber;
      tdReg.appendChild(UI.linkCell(s.registerNumber, () => {
        close();
        AdminView.openStudentDetail(content, s.registerNumber);
      }));
      tr.appendChild(tdReg);

      const tdName = document.createElement("td");
      tdName.appendChild(UI.linkCell(s.name, () => {
        close();
        AdminView.openStudentDetail(content, s.registerNumber);
      }));
      tr.appendChild(tdName);

      const tdBranch = document.createElement("td");
      tdBranch.className = "muted";
      tdBranch.textContent = s.branch || "—";
      tr.appendChild(tdBranch);

      const tdTrack = document.createElement("td");
      tdTrack.className = "muted";
      tdTrack.textContent = s.track;
      tr.appendChild(tdTrack);

      const tdScore = document.createElement("td");
      tdScore.className = "mono";
      const scoreStr = s.scoreDisplay || String(s.score);
      tdScore.textContent = scoreStr.replace(/%$/, "");
      tr.appendChild(tdScore);

      const tdAction = document.createElement("td");
      const unwatchBtn = document.createElement("button");
      unwatchBtn.className = "watchlist-mini-btn active";
      unwatchBtn.innerHTML = `<i data-lucide="eye-off"></i><span>Remove</span>`;
      unwatchBtn.addEventListener("click", async () => {
        await Api.post("/api/admin/watchlist/toggle", { registerNumber: s.registerNumber, watchlist: false });
        close();
        AdminView.openWatchlistModal(content);
        AdminView.render(content);
      });
      tdAction.appendChild(unwatchBtn);
      tr.appendChild(tdAction);

      tbody.appendChild(tr);
    });
    body.appendChild(table);
    UI.refreshIcons();
  },

  async openRiskModal(content) {
    const d = await Api.get(`/api/admin/risk?track=${encodeURIComponent(AdminView.trackFilter)}`);
    const { body, close } = UI.openModal({
      title: "Students at risk",
      subtitle: `${AdminView.trackFilter} · ${d.students.length} students with critical performance (< 50)`,
      size: "large",
    });

    if (!d.students || d.students.length === 0) {
      body.innerHTML = `
        <div style="text-align:center; padding: 40px 20px; color: var(--text-muted);">
          <i data-lucide="check-circle-2" style="width:36px; height:36px; stroke: var(--teal); margin-bottom:12px; display:inline-block;"></i>
          <p style="font-size:14px; margin-bottom:6px; color:var(--text-primary); font-weight:500;">No students at risk</p>
          <p style="font-size:12px; max-width:320px; margin:0 auto;">All students in this track have scores at or above 50.</p>
        </div>
      `;
      UI.refreshIcons();
      return;
    }

    const lowest = d.students[0].score;
    const avgRisk = Math.round(d.students.reduce((acc, s) => acc + s.score, 0) / d.students.length * 10) / 10;

    body.innerHTML = `
      <div class="modal-summary" style="margin-bottom:18px;">
        <div><div class="modal-summary-value" style="color:${PALETTE.coral}">${d.students.length}</div><div class="modal-summary-label">students at risk</div></div>
        <div><div class="modal-summary-value" style="color:${PALETTE.coral}">${lowest}</div><div class="modal-summary-label">lowest score</div></div>
        <div><div class="modal-summary-value" style="color:${PALETTE.amber}">${avgRisk}</div><div class="modal-summary-label">at-risk group avg</div></div>
      </div>
      <table class="data-table">
        <thead><tr><th>Register No.</th><th>Student</th><th>Branch</th><th>Track</th><th>Score</th><th>Lowest module</th><th>Watchlist</th></tr></thead>
        <tbody></tbody>
      </table>
    `;

    const tbody = body.querySelector("tbody");
    d.students.forEach((s) => {
      const tr = document.createElement("tr");

      const tdReg = document.createElement("td");
      tdReg.className = "mono";
      tdReg.style.color = PALETTE.coral;
      tdReg.appendChild(UI.linkCell(s.registerNumber, () => {
        close();
        AdminView.openStudentDetail(content, s.registerNumber);
      }));
      tr.appendChild(tdReg);

      const tdName = document.createElement("td");
      tdName.appendChild(UI.linkCell(s.name, () => {
        close();
        AdminView.openStudentDetail(content, s.registerNumber);
      }));
      tr.appendChild(tdName);

      const tdBranch = document.createElement("td");
      tdBranch.className = "muted";
      tdBranch.textContent = s.branch || "—";
      tr.appendChild(tdBranch);

      const tdTrack = document.createElement("td");
      tdTrack.className = "muted";
      tdTrack.textContent = s.track;
      tr.appendChild(tdTrack);

      const tdScore = document.createElement("td");
      tdScore.className = "mono";
      tdScore.style.color = PALETTE.coral;
      tdScore.style.fontWeight = "600";
      tdScore.textContent = s.scoreDisplay || String(s.score);
      tr.appendChild(tdScore);

      const tdMod = document.createElement("td");
      tdMod.className = "muted";
      tdMod.style.fontSize = "12px";
      tdMod.textContent = s.weakestModule;
      tr.appendChild(tdMod);

      const tdAction = document.createElement("td");
      const wlBtn = document.createElement("button");
      wlBtn.className = "watchlist-mini-btn" + (s.onWatchlist ? " active" : "");
      wlBtn.innerHTML = `<i data-lucide="eye"></i><span>${s.onWatchlist ? "Watching" : "+ Watch"}</span>`;
      wlBtn.addEventListener("click", async () => {
        wlBtn.disabled = true;
        try {
          const res = await Api.post("/api/admin/watchlist/toggle", { registerNumber: s.registerNumber });
          s.onWatchlist = res.onWatchlist;
          wlBtn.classList.toggle("active", s.onWatchlist);
          wlBtn.querySelector("span").textContent = s.onWatchlist ? "Watching" : "+ Watch";
          AdminView.render(content);
        } catch (err) {
          console.error(err);
        } finally {
          wlBtn.disabled = false;
        }
      });
      tdAction.appendChild(wlBtn);
      tr.appendChild(tdAction);

      tbody.appendChild(tr);
    });
    UI.refreshIcons();
  },
};

const TRACK_FILTER_OPTIONS = ["All tracks", "AI Track", "DevOps Track"];
