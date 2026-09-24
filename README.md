# Cohort Dashboard (FastAPI + HTML/CSS/JS)

Converted from the original `student-view.jsx` React mock into a modular
FastAPI backend + vanilla HTML/CSS/JS frontend. Behaviour, data, and the
visual design (dark theme, Manrope/JetBrains Mono, teal/amber/coral palette)
match the original 1:1.

## Run it

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Then open http://127.0.0.1:8000

## Layout

```
student_dashboard/
├── main.py                  # FastAPI app: mounts static files, renders the SPA shell
├── data.py                  # ALL data lives here — mock records + every derivation
│                             # function ported from the JSX (buildStats, buildAllTracksData,
│                             # the deterministic per-student drill-down PRNG, etc.)
├── requirements.txt
├── routers/
│   ├── admin.py              # /api/admin/*      — Admin overview, modals, student drill-down
│   ├── instructor.py         # /api/instructor/*  — syllabus overview + Add Topic form
│   └── student.py            # /api/student/*     — roster + profile drill-down + "my dashboard"
├── templates/
│   └── index.html            # single-page shell: sidebar + <template> partials cloned by JS
└── static/
    ├── css/style.css         # design tokens as CSS variables, ported from the JSX palette
    └── js/
        ├── api.js            # fetch() wrapper
        ├── ui.js             # shared DOM builders: stat cards, track filter, modal shell
        ├── charts.js         # Chart.js wrappers standing in for recharts
        ├── admin.js          # Admin tab logic
        ├── instructor.js     # Instructor tab logic
        ├── student.js        # Student tab logic (roster + shared profile detail page,
        │                     # also reused by the admin drill-down)
        └── app.js            # top-level sidebar router
```

## Notes on the conversion

- **Data injection**: every mock record and derivation formula
  (`buildAllTracksData`, `buildStats`, the `mulberry32`/`hashString`
  deterministic RNG used for per-student drill-downs, etc.) lives in
  `data.py`. Routers only shape data for the API — they never hardcode
  values.
- **Instructor "Add topic"**: the original React `useState` for topics is
  now server-side in-memory state (`data._TOPICS_BY_TRACK`) mutated via
  `POST /api/instructor/topics`, so it behaves the same for a single running
  server. Swap this for a real database if you need persistence across
  restarts or multiple workers.
- **Charts**: recharts (Bar/Pie/Line) → Chart.js, styled to match the same
  colors, fonts and tooltips.
- **Icons**: lucide-react → the `lucide` web build, referenced via
  `data-lucide="..."` attributes and refreshed with `lucide.createIcons()`
  after each render.
- One component in the original file, `StudentDashboard` (a "my own
  dashboard" view for a logged-in student), was defined but never actually
  rendered anywhere in the original `<App/>` — it was dead code. Its data
  was preserved as `GET /api/student/dashboard` in case you want to wire up
  a "My Dashboard" nav item later.

## Weightage

The student profile's **Weights** card and **Performance** card (total weightage) are loaded
live from MongoDB via `GET /api/student/weights/{registerNumber}`. No weight
topics are hardcoded — the card shows only what is stored for the student, and
the total is the sum of those rows. The exact document shape, rules, API
contract and mongosh snippets are in [`DATABASE.md`](DATABASE.md).
