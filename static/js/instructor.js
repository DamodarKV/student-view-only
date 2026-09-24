// instructor.js — Instructor tab: syllabus overview + Add Topic modal + Status Dropdown & Navigation.
const InstructorView = {
  trackFilter: "All tracks",
  statusFilter: "all", // 'all' | 'Completed' | 'In progress' | 'Upcoming'

  async render(content) {
    console.log("[InstructorView] rendering Instructor page", { trackFilter: InstructorView.trackFilter });
    const overview = await Api.get(`/api/instructor/overview?track=${encodeURIComponent(InstructorView.trackFilter)}`);

    const frag = UI.clone("tpl-instructor");
    const r = UI.refs(frag);
    r.subtitle.textContent = `Syllabus & topics completed — ${InstructorView.trackFilter.toLowerCase()}`;

    UI.buildTrackFilter(r["track-filter"], {
      value: InstructorView.trackFilter,
      options: TRACK_FILTER_OPTIONS,
      onChange: (opt) => {
        InstructorView.trackFilter = opt;
        InstructorView.render(content);
      },
    });

    r["add-exam-btn"].addEventListener("click", () => InstructorView.openAddExamModal(overview, content));
    if (r["add-assignment-btn"]) {
      r["add-assignment-btn"].addEventListener("click", () => InstructorView.openAddAssignmentModal(content));
    }

    // Render Stat Cards
    UI.renderStatGrid(r.stats, overview.stats);

    // Build Table Header
    const headRow = document.createElement("tr");
    ["Topic", ...(overview.showTrackColumn ? ["Track"] : []), "Status", "Date completed"].forEach((h) => {
      const th = document.createElement("th");
      th.textContent = h;
      headRow.appendChild(th);
    });
    r["topics-head"].appendChild(headRow);

    // Status Navigation Pills (All, 1- Completed, 2- In progress, 3- Upcoming)
    const renderStatusNav = () => {
      if (!r["status-nav"]) return;
      r["status-nav"].innerHTML = "";

      const totalCount = overview.topics.length;
      const completedCount = overview.topics.filter((t) => t.status === "Completed").length;
      const inProgressCount = overview.topics.filter((t) => t.status === "In progress").length;
      const upcomingCount = overview.topics.filter((t) => t.status === "Upcoming").length;

      const navOptions = [
        { key: "all", label: "All", count: totalCount },
        { key: "Completed", label: "1- Completed", count: completedCount, color: "var(--teal)" },
        { key: "In progress", label: "2- In progress", count: inProgressCount, color: "var(--amber)" },
        { key: "Upcoming", label: "3- Upcoming", count: upcomingCount, color: "var(--text-muted)" },
      ];

      navOptions.forEach((opt) => {
        const btn = document.createElement("button");
        btn.className = "pill" + (InstructorView.statusFilter === opt.key ? " active" : "");
        if (opt.color) btn.style.setProperty("--pill-color", opt.color);

        const labelSpan = document.createElement("span");
        labelSpan.textContent = opt.label;
        btn.appendChild(labelSpan);

        const countSpan = document.createElement("span");
        countSpan.className = "pill-count";
        countSpan.textContent = opt.count;
        btn.appendChild(countSpan);

        btn.addEventListener("click", () => {
          InstructorView.statusFilter = opt.key;
          renderStatusNav();
          renderTopicRows();
        });

        r["status-nav"].appendChild(btn);
      });
    };

    // Render Topics Rows
    const renderTopicRows = () => {
      r["topics-rows"].innerHTML = "";

      const filteredTopics = overview.topics.filter((t) => {
        if (InstructorView.statusFilter === "all") return true;
        return t.status === InstructorView.statusFilter;
      });

      if (filteredTopics.length === 0) {
        const emptyTr = document.createElement("tr");
        const emptyTd = document.createElement("td");
        emptyTd.colSpan = overview.showTrackColumn ? 4 : 3;
        emptyTd.className = "muted";
        emptyTd.style.textAlign = "center";
        emptyTd.style.padding = "24px 8px";
        emptyTd.textContent = `No topics with status "${InstructorView.statusFilter}" found.`;
        emptyTr.appendChild(emptyTd);
        r["topics-rows"].appendChild(emptyTr);
        return;
      }

      const statusClassMap = {
        Completed: "status-completed",
        "In progress": "status-in-progress",
        Upcoming: "status-upcoming",
      };

      filteredTopics.forEach((t) => {
        const tr = document.createElement("tr");

        // 1. Topic Title
        const tdTopic = document.createElement("td");
        tdTopic.textContent = t.topic;
        tr.appendChild(tdTopic);

        // 2. Track (if All tracks)
        if (overview.showTrackColumn) {
          const tdTrack = document.createElement("td");
          tdTrack.className = "muted";
          tdTrack.textContent = t.track;
          tr.appendChild(tdTrack);
        }

        // 3. Status Column: Dropdown selector (1- Completed, 2- In progress, 3- Upcoming)
        const tdStatus = document.createElement("td");
        const selectWrap = document.createElement("div");
        selectWrap.className = `status-select-wrap ${statusClassMap[t.status] || "status-completed"}`;

        const dot = document.createElement("span");
        dot.className = "status-select-dot";
        selectWrap.appendChild(dot);

        const select = document.createElement("select");
        select.className = "status-select";
        select.title = "Change topic status";

        const options = [
          { value: "Completed", label: "1- Completed" },
          { value: "In progress", label: "2- In progress" },
          { value: "Upcoming", label: "3- Upcoming" },
        ];

        options.forEach((opt) => {
          const optEl = document.createElement("option");
          optEl.value = opt.value;
          optEl.textContent = opt.label;
          if (t.status === opt.value) optEl.selected = true;
          select.appendChild(optEl);
        });

        const chevron = document.createElement("i");
        chevron.setAttribute("data-lucide", "chevron-down");
        chevron.className = "status-select-chevron";

        const tdDate = document.createElement("td");
        tdDate.className = "mono muted";
        tdDate.textContent = t.date || "—";

        select.addEventListener("change", async () => {
          const newStatus = select.value;
          if (newStatus === t.status) return;

          selectWrap.className = `status-select-wrap ${statusClassMap[newStatus] || "status-completed"}`;
          select.disabled = true;

          try {
            await Api.post("/api/instructor/topics/status", {
              topic: t.topic,
              track: t.track,
              status: newStatus,
            });

            t.status = newStatus;

            if (newStatus === "Completed" && (!t.date || t.date === "—")) {
              const now = new Date();
              const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
              t.date = `${months[now.getMonth()]} ${now.getDate()}, ${now.getFullYear()}`;
            } else if (newStatus === "Upcoming") {
              t.date = "—";
            }
            tdDate.textContent = t.date;

            // Refresh overview stats
            const freshOverview = await Api.get(
              `/api/instructor/overview?track=${encodeURIComponent(InstructorView.trackFilter)}`
            );
            overview.stats = freshOverview.stats;
            overview.topics = freshOverview.topics;
            UI.renderStatGrid(r.stats, overview.stats);

            renderStatusNav();
            if (InstructorView.statusFilter !== "all" && InstructorView.statusFilter !== newStatus) {
              renderTopicRows();
            }
          } catch (err) {
            console.error("Failed to update status:", err);
            select.value = t.status;
            selectWrap.className = `status-select-wrap ${statusClassMap[t.status] || "status-completed"}`;
          } finally {
            select.disabled = false;
          }
        });

        selectWrap.appendChild(select);
        selectWrap.appendChild(chevron);
        tdStatus.appendChild(selectWrap);
        tr.appendChild(tdStatus);

        // 4. Date Completed
        tr.appendChild(tdDate);

        r["topics-rows"].appendChild(tr);
      });

      UI.refreshIcons();
    };

    renderStatusNav();
    renderTopicRows();

    UI.clear(content);
    content.appendChild(frag);
    UI.refreshIcons();
  },

  openAddExamModal(overview, content) {
    const { body, close } = UI.openModal({ title: "Add Exam", size: "narrow" });
    body.appendChild(UI.clone("tpl-add-exam-modal"));
    const r = UI.refs(body);

    if (r.track) {
      r.track.value = InstructorView.trackFilter === "DevOps Track" ? "DevOps Track" : "AI Track";
    }

    const syncSubmitEnabled = () => {
      r.submit.disabled = r.topic.value.trim().length === 0;
    };
    syncSubmitEnabled();
    r.topic.addEventListener("input", syncSubmitEnabled);

    r.cancel.addEventListener("click", close);

    r.submit.addEventListener("click", async () => {
      const topic = r.topic.value.trim();
      const track = r.track.value;
      if (!topic) return;

      r.submit.disabled = true;
      try {
        await Api.post("/api/instructor/exams", {
          topic,
          track,
        });
        close();
        InstructorView.render(content);
      } catch (err) {
        alert(err.detail || err.message || "Failed to add exam.");
        r.submit.disabled = false;
      }
    });
  },

  async openAddAssignmentModal(content) {
    const { body, close } = UI.openModal({
      title: "Add Assignment",
      subtitle: "Create assignments for students",
      size: "medium",
    });

    const frag = UI.clone("tpl-add-assignment-modal");
    const r = UI.refs(frag);

    // Populate student register numbers from student roster
    try {
      const rosterData = await Api.get("/api/student/roster");
      const students = rosterData.students || [];
      students.forEach((s) => {
        const opt = document.createElement("option");
        opt.value = s.registerNumber;
        opt.textContent = `${s.registerNumber} - ${s.name}`;
        r["student-regno"].appendChild(opt);
      });
    } catch (err) {
      console.error("Failed to load student roster for assignments:", err);
    }

    // Helper to render assignment table rows
    const rowsContainer = r["assignment-rows"];
    let rowIdCounter = 0;

    const addRow = (defaultTopic = "", defaultTrack = "AI") => {
      rowIdCounter++;
      const tr = document.createElement("tr");
      tr.dataset.rowId = rowIdCounter;

      tr.innerHTML = `
        <td class="mono muted row-seq">${rowsContainer.children.length + 1}</td>
        <td>
          <input class="field-input input-topic" type="text" placeholder="e.g. ChromaDB Vector Store" value="${defaultTopic}" style="width:100%; padding:6px 10px; font-size:13px;" />
        </td>
        <td>
          <select class="field-input select-track" style="width:100%; padding:6px 10px; font-size:13px;">
            <option value="AI" ${defaultTrack === "AI" ? "selected" : ""}>AI</option>
            <option value="DevOps" ${defaultTrack === "DevOps" ? "selected" : ""}>DevOps</option>
          </select>
        </td>
        <td style="text-align:center;">
          <button class="btn-delete-row" title="Remove row" style="background:none; border:none; color:#E2604F; cursor:pointer; padding:4px;">
            <i data-lucide="trash-2" style="width:16px; height:16px;"></i>
          </button>
        </td>
      `;

      const deleteBtn = tr.querySelector(".btn-delete-row");
      deleteBtn.addEventListener("click", () => {
        if (rowsContainer.children.length <= 1) {
          alert("At least one assignment row is required.");
          return;
        }
        tr.remove();
        // Update sequence numbers
        Array.from(rowsContainer.children).forEach((childTr, idx) => {
          const seqTd = childTr.querySelector(".row-seq");
          if (seqTd) seqTd.textContent = idx + 1;
        });
      });

      rowsContainer.appendChild(tr);
      UI.refreshIcons();
    };

    // Initial 1 default row
    addRow();

    r["add-row-btn"].addEventListener("click", () => addRow());
    r.cancel.addEventListener("click", close);

    r.submit.addEventListener("click", async () => {
      const selectedRegNo = r["student-regno"].value;
      const rowElements = Array.from(rowsContainer.querySelectorAll("tr"));

      const assignmentsList = [];
      for (const rowEl of rowElements) {
        const topicInput = rowEl.querySelector(".input-topic");
        const trackSelect = rowEl.querySelector(".select-track");

        const topicVal = topicInput ? topicInput.value.trim() : "";
        const trackVal = trackSelect ? trackSelect.value : "AI";

        if (!topicVal) {
          alert("Please enter a topic name for all assignment rows.");
          if (topicInput) topicInput.focus();
          return;
        }
        if (!["AI", "DevOps"].includes(trackVal)) {
          alert("Track must be either AI or DevOps.");
          return;
        }

        assignmentsList.push({
          topic: topicVal,
          track: trackVal,
          status: "PENDING",
          score: null,
        });
      }

      if (assignmentsList.length === 0) {
        alert("Please add at least one assignment.");
        return;
      }

      r.submit.disabled = true;
      try {
        let payload = {};
        if (selectedRegNo === "ALL") {
          // Send for all roster students
          const rosterRes = await Api.get("/api/student/roster");
          const allRegNos = (rosterRes.students || []).map((s) => s.registerNumber);
          payload = {
            assignments: assignmentsList,
            studentRegisterNumbers: allRegNos,
          };
        } else {
          // Send for single chosen student
          payload = {
            assignments: assignmentsList.map((a) => ({
              ...a,
              studentRegisterNumber: selectedRegNo,
            })),
          };
        }

        await Api.post("/api/assignments", payload);
        close();
        InstructorView.render(content);
      } catch (err) {
        console.error("Failed to save assignments:", err);
        alert("Failed to save assignments: " + (err.detail || err.message));
      } finally {
        r.submit.disabled = false;
      }
    });

    body.appendChild(frag);
    UI.refreshIcons();
  },
};

