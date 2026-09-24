// student.js — Student tab: roster list + shared profile detail page.
const StudentView = {
  charts: [],

  async renderRoster(content) {
    console.log("[StudentView] rendering Student Roster page");
    const { students } = await Api.get("/api/student/roster");
    const frag = UI.clone("tpl-student-roster");
    const r = UI.refs(frag);

    let currentSort = "avg-desc"; // Default: scores high to low

    const sortFn = (a, b) => {
      if (currentSort === "avg-desc") return (Number(b.average) || 0) - (Number(a.average) || 0);
      if (currentSort === "avg-asc") return (Number(a.average) || 0) - (Number(b.average) || 0);
      if (currentSort === "weightage-desc") return (Number(b.totalWeightage) || 0) - (Number(a.totalWeightage) || 0);
      if (currentSort === "name-asc") return (a.name || "").localeCompare(b.name || "");
      return 0;
    };

    const updateRows = (list) => {
      const sortedList = [...list].sort(sortFn);
      r.subtitle.textContent = `${sortedList.length} of ${students.length} students · sorted by score high to low`;
      UI.clear(r.rows);
      sortedList.forEach((s) => {
        const tr = document.createElement("tr");

        const navigateToStudent = () => {
          if (window.AppRouter) {
            window.AppRouter.navigate("student", s.registerNumber);
          } else {
            StudentView.renderProfileDetail(content, s.registerNumber, {
              backLabel: "Back to student list",
              onBack: () => StudentView.renderRoster(content),
            });
          }
        };

        const tdReg = document.createElement("td");
        tdReg.className = "mono";
        tdReg.appendChild(UI.linkCell(s.registerNumber, navigateToStudent));
        tr.appendChild(tdReg);

        const tdName = document.createElement("td");
        tdName.style.cursor = "pointer";
        tdName.appendChild(UI.linkCell(s.name, navigateToStudent));
        tdName.addEventListener("click", (e) => {
          if (e.target === tdName) {
            navigateToStudent();
          }
        });
        tr.appendChild(tdName);

        const tdBranch = document.createElement("td");
        tdBranch.className = "muted";
        tdBranch.textContent = s.branch || "—";
        tr.appendChild(tdBranch);

        const tdAi = document.createElement("td");
        tdAi.className = "mono";
        tdAi.textContent = s.aiScoreDisplay || "—";
        tr.appendChild(tdAi);

        const tdDevops = document.createElement("td");
        tdDevops.className = "mono";
        tdDevops.textContent = s.hasDevops ? (s.devopsScoreDisplay || "—") : "—";
        tr.appendChild(tdDevops);

        const tdAvg = document.createElement("td");
        tdAvg.className = "mono";
        tdAvg.style.fontWeight = "600";
        tdAvg.style.color = s.statusColor;
        tdAvg.textContent = s.averageDisplay || (s.average !== undefined ? String(s.average) : "—");
        tr.appendChild(tdAvg);

        const tdPerformance = document.createElement("td");
        tdPerformance.className = "mono";
        if (s.hasWeights) {
          const total = Number(s.totalWeightage) || 0;
          tdPerformance.textContent = `${Number.isInteger(total) ? total : total.toFixed(1)}%`;
        } else {
          tdPerformance.textContent = "—";
        }
        tr.appendChild(tdPerformance);

        const tdWl = document.createElement("td");
        const wlBtn = document.createElement("button");
        wlBtn.className = "watchlist-mini-btn" + (s.onWatchlist ? " active" : "");
        wlBtn.title = s.onWatchlist ? "Remove from watchlist" : "Add to watchlist";
        wlBtn.innerHTML = `<i data-lucide="eye"></i><span>${s.onWatchlist ? "Watching" : "Watch"}</span>`;
        wlBtn.addEventListener("click", async (e) => {
          e.stopPropagation();
          wlBtn.disabled = true;
          try {
            const res = await Api.post("/api/student/watchlist/toggle", { registerNumber: s.registerNumber });
            s.onWatchlist = res.onWatchlist;
            wlBtn.classList.toggle("active", s.onWatchlist);
            wlBtn.title = s.onWatchlist ? "Remove from watchlist" : "Add to watchlist";
            wlBtn.querySelector("span").textContent = s.onWatchlist ? "Watching" : "Watch";
          } catch (err) {
            console.error("Watchlist error", err);
          } finally {
            wlBtn.disabled = false;
          }
        });
        tdWl.appendChild(wlBtn);
        tr.appendChild(tdWl);

        r.rows.appendChild(tr);
      });
      UI.refreshIcons();
    };

    const applyFilter = () => {
      const q = (r["search-input"] ? r["search-input"].value : "").trim().toLowerCase();
      const b = (r["branch-filter"] ? r["branch-filter"].value : "All");

      const filtered = students.filter((s) => {
        const matchesBranch = b === "All" || (s.branch && s.branch.toLowerCase() === b.toLowerCase());
        if (!matchesBranch) return false;
        if (!q) return true;
        return (
          (s.name && s.name.toLowerCase().includes(q)) ||
          (s.registerNumber && s.registerNumber.toLowerCase().includes(q)) ||
          (s.email && s.email.toLowerCase().includes(q)) ||
          (s.branch && s.branch.toLowerCase().includes(q))
        );
      });
      updateRows(filtered);
    };

    if (r["sort-select"]) {
      r["sort-select"].value = currentSort;
      r["sort-select"].addEventListener("change", (e) => {
        currentSort = e.target.value;
        applyFilter();
      });
    }

    if (r["search-input"]) {
      r["search-input"].addEventListener("input", applyFilter);
    }
    if (r["branch-filter"]) {
      r["branch-filter"].addEventListener("change", applyFilter);
    }

    updateRows(students);

    UI.clear(content);
    content.appendChild(frag);
    UI.refreshIcons();
  },

  /**
   * Renders the two-track profile detail page. `fetchUrl` decides whether
   * we're drilling in from the student roster or from the admin panel —
   * both endpoints return the same {student, detail} shape.
   */
  async renderProfileDetail(content, registerNumber, { backLabel, onBack, fetchUrl }) {
    console.log("[StudentView] rendering Student Profile Detail page", { registerNumber, fetchUrl });
    const url = fetchUrl || `/api/student/roster/${encodeURIComponent(registerNumber)}`;
    // Weights come straight from MongoDB via their own endpoint — no weight
    // topics are hardcoded in the frontend, the card shows exactly what is
    // stored for this student.
    const weightsUrl = `/api/student/weights/${encodeURIComponent(registerNumber)}`;
    const [{ student, detail }, weightsRes] = await Promise.all([
      Api.get(url),
      Api.get(weightsUrl).catch((err) => {
        console.error("[StudentView] Failed to load weights", err);
        return { weights: [], totalWeightage: 0 };
      }),
    ]);
    const weights = weightsRes.weights || [];

    StudentView.charts.forEach(Charts.destroy);
    StudentView.charts = [];

    const frag = UI.clone("tpl-student-profile");
    const r = UI.refs(frag);

    r["back-label"].textContent = backLabel;
    r.back.addEventListener("click", onBack);

    r.name.textContent = student.name;
    const branchPart = student.branch ? `Branch: ${student.branch} · ` : "";
    const mobilePart = student.mobile ? `Tel: ${student.mobile} · ` : "";
    r["meta-line"].textContent = `${student.registerNumber} · ${branchPart}${mobilePart}${student.email}`;

    const meta = CATEGORY_META[detail.status];
    r["status-pill"].style.setProperty("--pill-color", meta.color);
    r["status-dot"].style.background = meta.color;
    r["status-label"].textContent = `${meta.label} status`;

    let onWatchlist = Boolean(student.onWatchlist || (detail && detail.onWatchlist));
    const paintWatchlistBtn = () => {
      r["watchlist-btn"].classList.toggle("active", onWatchlist);
      r["watchlist-label"].textContent = onWatchlist ? "On watchlist" : "Add to watchlist";
    };
    paintWatchlistBtn();

    r["watchlist-btn"].addEventListener("click", async () => {
      r["watchlist-btn"].disabled = true;
      try {
        const res = await Api.post("/api/student/watchlist/toggle", { registerNumber: student.registerNumber });
        onWatchlist = res.onWatchlist;
        student.onWatchlist = onWatchlist;
        paintWatchlistBtn();
      } catch (err) {
        console.error("Failed to toggle watchlist", err);
      } finally {
        r["watchlist-btn"].disabled = false;
      }
    });

    const refreshProfile = () => StudentView.renderProfileDetail(content, registerNumber, { backLabel, onBack, fetchUrl });
    const profileOpts = { backLabel, onBack, fetchUrl, onRefreshProfile: refreshProfile };

    const totalWeightage = weights.reduce((sum, w) => sum + (Number(w.weightage) || 0), 0);
    const totalWeightageDisplay = Number.isInteger(totalWeightage) ? String(totalWeightage) : totalWeightage.toFixed(1);

    const stats = [
      { key: "ai", label: "AI Track score", value: student.aiScoreDisplay || (student.aiScore !== undefined ? String(student.aiScore) : "—"), unit: "", icon: "TrendingUp", fill: student.aiScore || 0, clickable: true, hint: "View & add topic scores" },
      { key: "devops", label: "DevOps Track score", value: student.hasDevops ? (student.devopsScoreDisplay || String(student.devopsScore)) : "—", unit: "", icon: "TrendingUp", fill: student.hasDevops ? (student.devopsScore || 0) : 0, clickable: Boolean(student.hasDevops), hint: student.hasDevops ? "View & add topic scores" : "" },
      { key: "avg", label: "Average score", value: student.averageDisplay || (detail.average !== undefined ? String(detail.average) : "—"), unit: "", icon: "CheckCircle2", fill: detail.average || 0 },
      { key: "weightage", label: "Performance", value: totalWeightageDisplay, unit: "%", icon: "Percent", fill: Math.min(totalWeightage, 100), clickable: true, hint: "View weights breakdown" },
      { key: "assignments", label: "Assignments completed", value: String(detail.assignmentsDone), unit: `of ${detail.totalAssignments}`, icon: "ClipboardCheck", clickable: true, hint: "View completed assignments" },
    ];
    UI.renderStatGrid(r.stats, stats, {
      ai: () => StudentView.openTopicScoreModal(content, student.registerNumber, "AI", profileOpts),
      devops: () => {
        if (student.hasDevops) {
          StudentView.openTopicScoreModal(content, student.registerNumber, "DevOps", profileOpts);
        }
      },
      weightage: () => {
        const weightsCard = r.weights.closest(".card");
        if (weightsCard) weightsCard.scrollIntoView({ behavior: "smooth", block: "center" });
      },
      assignments: () => StudentView.openAssignmentsCompletedModal(student, detail),
    });

    StudentView.renderWeightsTable(r.weights, weights, student.registerNumber, refreshProfile);

    r["add-weight-btn"].addEventListener("click", () => {
      StudentView.openAddWeightModal(student.registerNumber, refreshProfile);
    });

    r["feedback-note"].textContent = detail.feedback.note;
    r["feedback-meta"].textContent = `${detail.feedback.from} · ${detail.feedback.date}`;

    UI.clear(content);
    content.appendChild(frag);
    UI.refreshIcons();

    if (detail.aiModules) detail.aiModules.sort((a, b) => (Number(b.pct) || 0) - (Number(a.pct) || 0));
    if (detail.devopsModules) detail.devopsModules.sort((a, b) => (Number(b.pct) || 0) - (Number(a.pct) || 0));

    // Module bar charts must show only real assessment scores — pending or
    // scored Assignment Topics (synced into Scores.[AI|DevOps], tagged via
    // AssignmentTopics.[AI|DevOps] — see data.py/db.py) should not render
    // as bars here. Strict === true check on purpose: any module missing a
    // definitive hasScore flag (e.g. an older/uncached response) is treated
    // as NOT a real score rather than assumed to be one — fail closed, not
    // open. The full (unfiltered) detail.aiModules / detail.devopsModules
    // lists are left untouched below since the "Topic Scores" modal still
    // needs to show pending topics so scores can be added to them.
    const scoredAiModules = (detail.aiModules || []).filter((m) => m.hasScore === true);
    const scoredDevopsModules = (detail.devopsModules || []).filter((m) => m.hasScore === true);

    StudentView.charts.push(Charts.line(content.querySelector("[data-el='trend-chart']"), detail.scoreTrend, "week", "score", PALETTE.blue));
    StudentView.charts.push(Charts.horizontalBar(content.querySelector("[data-el='ai-modules-chart']"), scoredAiModules, "module", "pct", PALETTE.teal));
    content.querySelector("[data-el='ai-modules-box']").style.height = Math.max(200, scoredAiModules.length * 40) + "px";

    const devopsBox = content.querySelector("[data-el='devops-modules-box']");
    if (!detail.devopsModules || detail.devopsModules.length === 0) {
      // No DevOps entries at all (not even pending assignment topics) —
      // same "not enrolled" signal the original code used.
      devopsBox.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:13px;padding:30px;">Not enrolled in DevOps Track</div>`;
      devopsBox.style.height = "160px";
    } else if (scoredDevopsModules.length > 0) {
      StudentView.charts.push(Charts.horizontalBar(content.querySelector("[data-el='devops-modules-chart']"), scoredDevopsModules, "module", "pct", PALETTE.blue));
      devopsBox.style.height = Math.max(200, scoredDevopsModules.length * 40) + "px";
    } else {
      devopsBox.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:13px;padding:30px;">No scored DevOps assessments yet</div>`;
      devopsBox.style.height = "160px";
    }
  },

  /** Renders the "Weights" table (assessment type + weightage) with delete action per row. */
  renderWeightsTable(tbody, weights, registerNumber, onChanged) {
    UI.clear(tbody);
    const list = weights || [];

    if (list.length === 0) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="3" style="text-align:center; color:var(--text-muted); padding:16px;">No weights added yet. Click "+" to add one.</td>`;
      tbody.appendChild(tr);
      UI.refreshIcons();
      return;
    }

    list.forEach((w, idx) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="mono muted">${idx + 1}</td>
        <td style="font-weight:500;">${w.assessmentType}</td>
        <td class="mono" style="text-align:right;">${w.weightage}%</td>
      `;

      tbody.appendChild(tr);
    });
    UI.refreshIcons();
  },

  /** Opens the "Add Weightage" modal; calls onDone() after a successful save. */
  openAddWeightModal(registerNumber, onDone) {
    const { body, close } = UI.openModal({
      title: "Add Weightage",
      subtitle: `Reg No: ${registerNumber}`,
    });
    const frag = UI.clone("tpl-add-weight-modal");
    const r = UI.refs(frag);
    body.appendChild(frag);
    UI.refreshIcons();

    r.cancel.addEventListener("click", close);
    r.submit.addEventListener("click", async () => {
      const assessmentType = r["assessment-type"].value.trim();
      const weightage = parseFloat(r.weightage.value);

      if (!assessmentType) {
        alert("Please enter an assessment type.");
        return;
      }
      if (isNaN(weightage) || weightage < 0 || weightage > 100) {
        alert("Please enter a valid weightage between 0 and 100.");
        return;
      }

      r.submit.disabled = true;
      try {
        await Api.post("/api/student/weight", {
          registerNumber,
          assessmentType,
          weightage,
        });
        close();
        onDone();
      } catch (err) {
        alert("Error adding weightage: " + (err.detail || err.message));
      } finally {
        r.submit.disabled = false;
      }
    });
  },



  async openTopicScoreModal(content, registerNumber, track, profileOptions) {
    const url = profileOptions.fetchUrl || `/api/student/roster/${encodeURIComponent(registerNumber)}`;
    let { student, detail } = await Api.get(url);

    const isAi = track.toUpperCase() === "AI";
    const trackName = isAi ? "AI Track" : "DevOps Track";
    const trackKey = isAi ? "AI" : "DevOps";

    const { overlay, body, close } = UI.openModal({
      title: `${trackName} Topic Scores`,
      subtitle: `${student.name} · Reg No: ${student.registerNumber}`,
      size: "medium",
    });

    let currentStudent = student;
    let currentDetail = detail;

    const renderTable = (stud, det, flashTopicKey) => {
      UI.clear(body);
      const modules = isAi ? (det.aiModules || []) : (det.devopsModules || []);

      const summaryBox = document.createElement("div");
      summaryBox.className = "modal-summary";
      summaryBox.style.marginBottom = "16px";
      summaryBox.innerHTML = `
        <div><div class="modal-summary-value" style="color:var(--teal);">${isAi ? (stud.aiScoreDisplay || stud.aiScore) : (stud.devopsScoreDisplay || stud.devopsScore)}</div><div class="modal-summary-label">${trackName} Score</div></div>
        <div><div class="modal-summary-value">${modules.length}</div><div class="modal-summary-label">Total Topics</div></div>
      `;
      body.appendChild(summaryBox);

      const table = document.createElement("table");
      table.className = "data-table";
      table.style.width = "100%";
      table.innerHTML = `
        <thead>
          <tr>
            <th style="width:40px;">#</th>
            <th>Topic</th>
            <th style="width:140px; text-align:right;">Score</th>
          </tr>
        </thead>
        <tbody></tbody>
      `;
      const tbody = table.querySelector("tbody");

      if (modules.length === 0) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td colspan="3" style="text-align:center; color:var(--text-muted); padding:20px;">No topic scores found for this track.</td>`;
        tbody.appendChild(tr);
      } else {
        modules.forEach((m, idx) => {
          const tr = document.createElement("tr");
          const pct = Number(m.pct) || 0;
          const scoreColor = pct >= 75 ? PALETTE.teal : pct >= 50 ? PALETTE.amber : PALETTE.coral;
          const topicIdentifier = m.rawKey || m.module;

          if (flashTopicKey && topicIdentifier === flashTopicKey) {
            tr.classList.add("score-flash-success");
            setTimeout(() => tr.classList.remove("score-flash-success"), 1200);
          }

          const tdIdx = document.createElement("td");
          tdIdx.className = "mono muted";
          tdIdx.textContent = String(idx + 1);
          tr.appendChild(tdIdx);

          const tdName = document.createElement("td");
          tdName.style.fontWeight = "500";
          tdName.textContent = m.module || m.rawKey;
          tr.appendChild(tdName);

          const tdScore = document.createElement("td");
          tdScore.className = "mono";
          tdScore.style.textAlign = "right";

          const renderScoreCell = () => {
            UI.clear(tdScore);
            const scoreSpan = document.createElement("span");
            scoreSpan.style.fontWeight = "600";
            scoreSpan.style.color = m.hasScore ? scoreColor : "var(--text-muted)";
            scoreSpan.textContent = m.hasScore ? String(m.pct) : "--";
            tdScore.appendChild(scoreSpan);
          };

          renderScoreCell();
          tr.appendChild(tdScore);
          tbody.appendChild(tr);
        });
      }
      body.appendChild(table);
      UI.refreshIcons();
    };

    renderTable(student, detail);
    UI.refreshIcons();
  },

  openAssignmentsCompletedModal(student, detail) {
    const { overlay, body } = UI.openModal({
      title: "Assignments Completed Dashboard",
      subtitle: `${student.name} · Reg No: ${student.registerNumber}`,
      size: "medium",
    });



    let currentStudent = student;
    let currentDetail = detail;

    // NOTE: `aiModules` / `devopsModules` on the detail object are the
    // student's full list of topic/assessment scores — every topic they
    // have a score for, not the (much smaller) set of assignments that
    // were actually assigned to them. The "Assignments Completed" card
    // must reflect only real records from the `assignments` collection
    // (GET /api/assignments/student/:regNo), so that is the sole source
    // of truth for this modal's list. Topic scores are only used to fill
    // in a score for an assignment when the assignment record itself
    // doesn't carry one yet.
    const extractTopicScores = (det) => {
      const ai = (det.aiModules || []).map((m) => ({ ...m, track: "AI" }));
      const devops = (det.devopsModules || []).map((m) => ({ ...m, track: "DevOps" }));
      return [...ai, ...devops];
    };

    let allAssignments = [];

    const loadAssignmentsFromApi = async () => {
      try {
        const res = await Api.get(`/api/assignments/student/${encodeURIComponent(student.registerNumber)}`);
        const backendAssignments = res.assignments || [];
        const topicScores = extractTopicScores(currentDetail);

        allAssignments = backendAssignments.map((b) => {
          const track = b.track === "DevOps" ? "DevOps" : "AI";
          let pct = b.score;
          if (pct === undefined || pct === null) {
            const match = topicScores.find(
              (c) => (c.rawKey || c.module || "").toLowerCase() === (b.topic || "").toLowerCase() && c.track === track
            );
            if (match) pct = match.pct;
          }
          return {
            module: b.topic,
            rawKey: b.topic,
            track,
            pct,
            status: b.status || "PENDING",
            assignmentId: b.assignmentId,
            fromMongo: true,
          };
        });

        renderRows(select.value);
      } catch (err) {
        console.warn("Could not fetch assignments from API:", err);
        allAssignments = [];
        renderRows(select.value);
      }
    };

    // Filter controls section
    const filterContainer = document.createElement("div");
    filterContainer.style.display = "flex";
    filterContainer.style.alignItems = "center";
    filterContainer.style.justifyContent = "space-between";
    filterContainer.style.marginBottom = "16px";
    filterContainer.style.flexWrap = "wrap";
    filterContainer.style.gap = "12px";

    const leftControls = document.createElement("div");
    leftControls.style.display = "flex";
    leftControls.style.alignItems = "center";
    leftControls.style.gap = "8px";

    const label = document.createElement("label");
    label.style.fontSize = "13px";
    label.style.fontWeight = "600";
    label.style.color = "var(--text-muted)";
    label.textContent = "Track:";

    const select = document.createElement("select");
    select.className = "field-input";
    select.style.width = "140px";
    select.style.padding = "6px 10px";
    select.style.fontSize = "13px";
    select.style.borderRadius = "6px";

    select.innerHTML = `
      <option value="All">All Tracks</option>
      <option value="AI">AI</option>
      <option value="DevOps">DevOps</option>
    `;

    leftControls.appendChild(label);
    leftControls.appendChild(select);

    const countBadge = document.createElement("div");
    countBadge.style.fontSize = "13px";
    countBadge.style.fontWeight = "500";
    countBadge.style.color = "var(--text-muted)";

    filterContainer.appendChild(leftControls);
    filterContainer.appendChild(countBadge);
    body.appendChild(filterContainer);

    // Table container
    const tableWrap = document.createElement("div");
    tableWrap.style.overflowX = "auto";

    const table = document.createElement("table");
    table.className = "data-table";
    table.style.width = "100%";
    table.innerHTML = `
      <thead>
        <tr>
          <th style="width:50px;">#</th>
          <th>Topic</th>
          <th style="width:120px;">Track</th>
          <th style="width:100px; text-align:right;">Score</th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    const tbody = table.querySelector("tbody");
    tableWrap.appendChild(table);
    body.appendChild(tableWrap);

    const renderRows = (selectedTrack) => {
      UI.clear(tbody);
      let list = allAssignments;
      if (selectedTrack === "AI") {
        list = allAssignments.filter((a) => a.track === "AI");
      } else if (selectedTrack === "DevOps") {
        list = allAssignments.filter((a) => a.track === "DevOps");
      }

      countBadge.textContent = `Showing ${list.length} assignment${list.length === 1 ? "" : "s"}`;

      if (list.length === 0) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td colspan="4" style="text-align:center; color:var(--text-muted); padding:20px;">No assignments found for this track.</td>`;
        tbody.appendChild(tr);
        return;
      }

      list.forEach((item, idx) => {
        const tr = document.createElement("tr");
        const hasScore = item.pct !== undefined && item.pct !== null && item.pct !== "";
        const pct = hasScore ? Number(item.pct) : null;
        const scoreDisplay = hasScore ? (isNaN(pct) ? item.pct : pct) : "—";
        const scoreColor = !hasScore ? "var(--text-muted)" : (pct >= 75 ? PALETTE.teal : pct >= 50 ? PALETTE.amber : PALETTE.coral);
        const isAi = item.track === "AI";
        const badgeBg = isAi ? "rgba(63, 191, 166, 0.15)" : "rgba(76, 126, 255, 0.15)";
        const badgeColor = isAi ? PALETTE.teal : PALETTE.blue;

        tr.innerHTML = `
          <td class="mono muted">${idx + 1}</td>
          <td style="font-weight:500;">${item.module || item.rawKey}</td>
          <td>
            <span style="display:inline-block; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:600; background:${badgeBg}; color:${badgeColor};">
              ${item.track}
            </span>
          </td>
          <td class="mono" style="text-align:right; font-weight:600; color:${scoreColor};">${scoreDisplay}</td>
        `;

        tbody.appendChild(tr);
      });
      UI.refreshIcons();
    };



    select.addEventListener("change", (e) => {
      renderRows(e.target.value);
    });

    // Initial render with "All Tracks"
    renderRows("All");
    loadAssignmentsFromApi();
    UI.refreshIcons();
  },

};


// shared status color/label lookup (mirrors data.CATEGORY_META)
const CATEGORY_META = {
  critical: { label: "Critical", color: "#E2604F" },
  moderate: { label: "Moderate", color: "#E3A83B" },
  onTrack: { label: "On track", color: "#3FBFA6" },
};