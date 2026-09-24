// app.js — top-level router, mirrors the `view` state in the original App().
(function () {
  const content = document.getElementById("content");
  const navButtons = Array.from(document.querySelectorAll(".sidebar-item"));

  const sidebar = document.getElementById("sidebar");
  const sidebarOverlay = document.getElementById("sidebar-overlay");
  const mobileToggleBtn = document.getElementById("mobile-toggle-btn");
  const sidebarCloseBtn = document.getElementById("sidebar-close-btn");

  function closeMobileSidebar() {
    if (sidebar) sidebar.classList.remove("open");
    if (sidebarOverlay) sidebarOverlay.classList.remove("active");
  }

  function openMobileSidebar() {
    if (sidebar) sidebar.classList.add("open");
    if (sidebarOverlay) sidebarOverlay.classList.add("active");
  }

  if (mobileToggleBtn) mobileToggleBtn.addEventListener("click", openMobileSidebar);
  if (sidebarCloseBtn) sidebarCloseBtn.addEventListener("click", closeMobileSidebar);
  if (sidebarOverlay) sidebarOverlay.addEventListener("click", closeMobileSidebar);

  function parseCurrentRoute() {
    // 1. Check hash first: #student/DDAIISE05 or #/student/DDAIISE05 or #student or #admin or #instructor
    const hash = (window.location.hash || "").replace(/^#\/?/, "").trim();
    if (hash) {
      const parts = hash.split("/").filter(Boolean);
      return {
        view: parts[0] || "admin",
        param: parts[1] || null,
      };
    }

    // 2. Check pathname: /student/DDAIISE05 or /student or /instructor or /admin
    const pathname = (window.location.pathname || "").replace(/^\//, "").trim();
    if (pathname) {
      const parts = pathname.split("/").filter(Boolean);
      const first = parts[0];
      if (first === "student" || first === "admin" || first === "instructor") {
        return {
          view: first,
          param: parts[1] || null,
        };
      }
    }

    return { view: "admin", param: null };
  }

  const AppRouter = {
    navigate(view, param = null, replace = false) {
      let targetPath = `/${view}`;
      if (param) targetPath += `/${encodeURIComponent(param)}`;

      if (window.location.pathname !== targetPath) {
        try {
          if (replace) {
            window.history.replaceState({ view, param }, "", targetPath);
          } else {
            window.history.pushState({ view, param }, "", targetPath);
          }
        } catch (e) {
          // Fallback to hash if history API fails in restricted environments
          window.location.hash = param ? `${view}/${param}` : view;
        }
      }
      AppRouter.renderRoute(view, param);
    },

    renderRoute(view, param = null) {
      closeMobileSidebar();
      const activeNavView = view === "student" ? "student" : view;
      navButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.view === activeNavView));

      if (view === "student") {
        if (param) {
          console.log(`[app] navigating to student individual dashboard for: ${param}`);
          StudentView.renderProfileDetail(content, param, {
            backLabel: "Back to student list",
            onBack: () => AppRouter.navigate("student"),
          });
        } else {
          console.log(`[app] navigating to student roster page`);
          StudentView.renderRoster(content);
        }
      } else if (view === "instructor") {
        console.log(`[app] navigating to instructor page`);
        InstructorView.render(content);
      } else {
        console.log(`[app] navigating to admin page`);
        AdminView.render(content);
      }
      UI.refreshIcons();
    },

    handleCurrentRoute() {
      const { view, param } = parseCurrentRoute();
      AppRouter.renderRoute(view, param);
    },
  };

  window.AppRouter = AppRouter;

  window.addEventListener("popstate", () => {
    AppRouter.handleCurrentRoute();
  });

  window.addEventListener("hashchange", () => {
    AppRouter.handleCurrentRoute();
  });

  navButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      AppRouter.navigate(btn.dataset.view);
    });
  });

  // Initial route handling on page load / refresh
  AppRouter.handleCurrentRoute();

  Api.get("/api/admin/status")
    .then((st) => {
      const el = document.getElementById("sidebar-status-text");
      if (el) {
        el.textContent = `${st.studentCount} students · ${st.dataSource}`;
      }
    })
    .catch(() => {});
})();
