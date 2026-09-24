// ui.js — small reusable DOM builders shared across every view.
const UI = {
  refreshIcons() {
    if (window.lucide) window.lucide.createIcons();
  },

  clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  },

  clone(tplId) {
    const tpl = document.getElementById(tplId);
    return tpl.content.cloneNode(true);
  },

  // Returns a map of data-el="x" -> element, scoped under `root`.
  refs(root) {
    const map = {};
    root.querySelectorAll("[data-el]").forEach((el) => {
      map[el.dataset.el] = el;
    });
    return map;
  },

  /** Renders one stat card (matches the StatCard component). */
  buildStatCard(stat, onClick) {
    const frag = UI.clone("tpl-stat-card");
    const r = UI.refs(frag);
    r.label.textContent = stat.label;
    r.icon.setAttribute("data-lucide", UI.iconSlug(stat.icon));
    r.value.textContent = stat.value;
    if (stat.tone === "amber") r.value.classList.add("tone-amber");
    if (stat.tone === "coral") r.value.classList.add("tone-coral");
    r.unit.textContent = stat.unit;
    if (typeof stat.fill === "number") {
      r["fill-track"].hidden = false;
      r.fill.style.width = `${stat.fill}%`;
    }
    if (stat.clickable && onClick) {
      r.card.classList.add("clickable");
      r.card.setAttribute("role", "button");
      r.card.tabIndex = 0;
      r.card.addEventListener("click", onClick);
      r.card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") onClick();
      });
      if (stat.hint) r.hint.textContent = stat.hint;
      r.hint.hidden = false;
    }
    return frag;
  },

  iconSlug(name) {
    // lucide-react component names -> lucide web-component kebab-case slugs
    const map = {
      Users: "users",
      TrendingUp: "trending-up",
      Eye: "eye",
      AlertTriangle: "alert-triangle",
      ClipboardList: "clipboard-list",
      CheckCircle2: "check-circle-2",
      ClipboardCheck: "clipboard-check",
      Calendar: "calendar",
      Flame: "flame",
      Percent: "percent",
    };
    return map[name] || "circle";
  },

  renderStatGrid(container, stats, onClickMap = {}) {
    UI.clear(container);
    container.classList.remove("cols-5", "cols-4");
    if (stats.length === 5) container.classList.add("cols-5");
    if (stats.length === 4) container.classList.add("cols-4");
    stats.forEach((s) => {
      container.appendChild(UI.buildStatCard(s, onClickMap[s.key]));
    });
    UI.refreshIcons();
  },

  /** Track filter dropdown (matches TrackFilter component). */
  buildTrackFilter(container, { value, options, onChange }) {
    UI.clear(container);
    const frag = UI.clone("tpl-track-filter");
    const r = UI.refs(frag);
    r.toggle.textContent = value + " ";
    const chevron = document.createElement("i");
    chevron.setAttribute("data-lucide", "chevron-down");
    r.toggle.appendChild(chevron);

    const renderMenu = () => {
      UI.clear(r.menu);
      options.forEach((opt) => {
        const btn = document.createElement("button");
        btn.className = "track-filter-option" + (opt === value ? " active" : "");
        btn.textContent = opt;
        btn.addEventListener("click", () => {
          r.menu.hidden = true;
          onChange(opt);
        });
        r.menu.appendChild(btn);
      });
    };
    renderMenu();

    r.toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      r.menu.hidden = !r.menu.hidden;
    });
    document.addEventListener("click", () => { r.menu.hidden = true; });

    container.appendChild(frag);
    UI.refreshIcons();
  },

  /** Generic modal shell (matches the *Modal components' overlay/card chrome). */
  openModal({ title, subtitle, size = "" }) {
    const frag = UI.clone("tpl-modal-shell");
    const r = UI.refs(frag);
    r.title.textContent = title;
    r.subtitle.textContent = subtitle || "";
    if (size) r.modal.classList.add(size);

    document.body.appendChild(frag);
    // frag is now detached from variable after appendChild moves it; re-query.
    const overlay = document.body.lastElementChild;
    const modalEl = overlay.querySelector("[data-el='modal']");
    const bodyEl = overlay.querySelector("[data-el='body']");
    const closeBtn = overlay.querySelector("[data-el='close']");

    const close = () => overlay.remove();
    overlay.addEventListener("click", close);
    modalEl.addEventListener("click", (e) => e.stopPropagation());
    closeBtn.addEventListener("click", close);

    return { overlay, body: bodyEl, close };
  },

  statusBadge(status, meta) {
    const span = document.createElement("span");
    span.className = "status-badge";
    span.style.setProperty("--badge-color", meta.color);
    const dot = document.createElement("span");
    dot.className = "status-badge-dot";
    span.appendChild(dot);
    span.appendChild(document.createTextNode(meta.label));
    return span;
  },

  linkCell(label, onClick) {
    const frag = UI.clone("tpl-student-name-cell");
    const r = UI.refs(frag);
    r.btn.textContent = label;
    r.btn.addEventListener("click", onClick);
    return frag;
  },
};
