"""
data.py
-------
Single source of truth for dashboard data.
Loads student performance records from MongoDB Atlas (with automatic fallback
to students_output_accuracy2.json). Every student detail, score, module score,
and cohort statistic is 100% matched to the original database records.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import average
import insert_mongo as db

logger = logging.getLogger("studentview.data")

# ---------------------------------------------------------------------------
# DESIGN TOKENS
# ---------------------------------------------------------------------------
PALETTE: Dict[str, str] = {
    "bg": "#10151C",
    "surface": "#171E27",
    "surfaceRaised": "#1D2530",
    "border": "#28313D",
    "textPrimary": "#EAEDF1",
    "textMuted": "#8D98A6",
    "teal": "#3FBFA6",
    "tealDim": "#2A8E7B",
    "amber": "#E3A83B",
    "coral": "#E2604F",
    "blue": "#5B8DEF",
}

TRACK_FILTER_OPTIONS: List[str] = ["All tracks", "AI Track", "DevOps Track"]

CATEGORY_META: Dict[str, Dict[str, str]] = {
    "critical": {"label": "Critical", "color": PALETTE["coral"]},
    "moderate": {"label": "Moderate", "color": PALETTE["amber"]},
    "onTrack": {"label": "On track", "color": PALETTE["teal"]},
}

PANEL_CATEGORY_META: Dict[str, Dict[str, str]] = {
    "selected": {"label": "Selected", "color": PALETTE["teal"]},
    "rejected": {"label": "Rejected", "color": PALETTE["coral"]},
}

TOPIC_STATUS_META: Dict[str, Dict[str, str]] = {
    "Completed": {"label": "Completed", "color": PALETTE["teal"]},
    "In progress": {"label": "In progress", "color": PALETTE["amber"]},
    "Upcoming": {"label": "Upcoming", "color": PALETTE["textMuted"]},
}

TRACK_OPTIONS: List[str] = ["AI Track", "DevOps Track"]
STATUS_OPTIONS: List[str] = ["Completed", "In progress", "Upcoming"]

MENTOR_NAMES: List[str] = ["A. Sharma", "P. Rao", "S. Nair", "R. Menon", "K. Das"]


def _format_score(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        f = float(v)
        return round(f, 2)
    except (ValueError, TypeError):
        return default


def _display_score(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
        if f.is_integer():
            return str(int(f))
        return f"{f:.2f}".rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return str(v)


def status_for_score(score: float) -> str:
    if score < 50:
        return "critical"
    if score < 75:
        return "moderate"
    return "onTrack"


def clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


# ---------------------------------------------------------------------------
# LOAD RAW DATA & BUILD ROSTER
# ---------------------------------------------------------------------------
RAW_STUDENTS, DATA_SOURCE = db.fetch_students_data()
average.sync_topics_from_students(RAW_STUDENTS)


def _normalize_weights(raw: Any) -> List[Dict[str, Any]]:
    """
    Cleans a raw Mongo `Weightage` list into [{"assessmentType", "weightage"}].
    Skips non-dict entries, blank types and non-numeric weights, and
    de-duplicates by assessment type (case-insensitive, first wins — the same
    match rule the write path uses). Shared by the roster and the live
    weights endpoint so both always agree on the total.
    """
    if not isinstance(raw, list):
        return []
    weights: List[Dict[str, Any]] = []
    seen = set()
    for w in raw:
        if not isinstance(w, dict):
            continue
        a_type = (w.get("AssessmentType") or "").strip()
        key = a_type.lower()
        if not a_type or key in seen:
            continue
        try:
            value = float(w.get("Weightage"))
        except (TypeError, ValueError):
            continue
        seen.add(key)
        weights.append({"assessmentType": a_type, "weightage": value})
    return weights


def parse_performance_score(val: Any) -> Optional[float]:
    """Safely parses a performance score from number or string percentage (e.g. '66.2%')."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.strip().rstrip("%").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def get_student_performance(s: Dict[str, Any]) -> Optional[float]:
    """
    Extracts the numeric Performance score (total weightage) for a student.
    Handles numeric totalWeightage, string percentage, or raw weights list.
    """
    if "totalWeightage" in s and s["totalWeightage"] is not None:
        parsed = parse_performance_score(s["totalWeightage"])
        if parsed is not None:
            return parsed

    raw_weights = s.get("weights")
    if raw_weights is None and "raw" in s and isinstance(s["raw"], dict):
        raw_weights = s["raw"].get("Weightage")
    if raw_weights is None and "Weightage" in s:
        raw_weights = s.get("Weightage")

    if raw_weights:
        normalized = _normalize_weights(raw_weights)
        if normalized:
            return round(sum(w["weightage"] for w in normalized), 2)

    return None


def classify_student_by_performance(performance_val: Any) -> str:
    """
    Classifies a student as 'Selected' or 'Rejected' based strictly on Performance score:
    - performance_score >= 50.0 -> 'Selected'
    - performance_score < 50.0 (or missing/null) -> 'Rejected'
    The 50% threshold is inclusive.
    """
    perf = parse_performance_score(performance_val)
    if perf is not None and perf >= 50.0:
        return "Selected"
    return "Rejected"


def _build_student_roster(raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    roster = []
    for idx, s in enumerate(raw_list):
        reg = s.get("RegNumber") or f"STU-{idx + 1:03d}"
        name = s.get("StudentName") or "Unknown Student"
        email = s.get("MailID") or f"{reg.lower()}@student.edu"
        mobile = s.get("MobileNumber", "")
        branch = s.get("Branch", "")

        avg_dict = s.get("AverageScore", {})
        ai_raw = avg_dict.get("AI")
        devops_raw = avg_dict.get("DevOps")
        agg_raw = avg_dict.get("Aggregate")

        ai_score = _format_score(ai_raw)
        has_devops = devops_raw is not None and devops_raw != ""
        devops_score = _format_score(devops_raw) if has_devops else None

        if agg_raw is not None and agg_raw != "":
            aggregate = _format_score(agg_raw)
        else:
            aggregate = round((ai_score + devops_score) / 2, 2) if has_devops else ai_score

        mentor = MENTOR_NAMES[idx % len(MENTOR_NAMES)]
        status = status_for_score(aggregate)

        watchlist_status = s.get("WatchListStatus", "")
        weights_list = _normalize_weights(s.get("Weightage"))
        has_weights = bool(weights_list)
        total_weightage = round(sum(w["weightage"] for w in weights_list), 2) if has_weights else None

        # Performance-based classification:
        # Performance >= 50 -> Selected, Performance < 50 (or missing/null) -> Rejected
        norm_student_status = classify_student_by_performance(total_weightage)

        on_watchlist = bool(
            watchlist_status and str(watchlist_status).strip().lower() in ("watchlist", "true", "yes")
        )
        '''
        _cleaned_scores = s.get("Scores", {})
        del_list = ["Amazon Backend","Tokeniser Algorithm","RAG"]
        for l in del_list:
            del _cleaned_scores["AI"][l]
        s["Scores"] = _cleaned_scores
        '''

        roster.append({
            "registerNumber": reg,
            "name": name,
            "email": email,
            "mobile": mobile,
            "branch": branch,
            "aiScore": ai_score,
            "devopsScore": devops_score if devops_score is not None else 0,
            "hasDevops": has_devops,
            "average": aggregate,
            "aiScoreDisplay": _display_score(ai_raw),
            "devopsScoreDisplay": _display_score(devops_raw),
            "averageDisplay": _display_score(aggregate),
            "mentor": mentor,
            "status": status,
            "studentStatus": norm_student_status,
            "statusColor": CATEGORY_META[status]["color"],
            "scores": s.get("Scores", {}),
            "weights": s.get("Weightage", []) if isinstance(s.get("Weightage", []), list) else [],
            "totalWeightage": total_weightage if total_weightage is not None else 0,
            "hasWeights": has_weights,
            "performance": total_weightage,
            "mentorFeedback": s.get("MentorFeedback") if isinstance(s.get("MentorFeedback"), dict) else None,
            "assignmentTopics": s.get("AssignmentTopics", {}),
            "attendance": s.get("Attandance", {}),
            "trackStatus": norm_student_status,
            "watchListStatus": watchlist_status,
            "onWatchlist": on_watchlist,
            "raw": s,
        })

    # Sort students from highest score to lowest score (high to low)
    roster.sort(key=lambda s: (s.get("average") if s.get("average") is not None else -1), reverse=True)
    return roster



STUDENT_ROSTER: List[Dict[str, Any]] = _build_student_roster(RAW_STUDENTS)


def reload_students():
    """Refreshes student and instructor data from MongoDB or local JSON."""
    global RAW_STUDENTS, DATA_SOURCE, STUDENT_ROSTER, RAW_DATA, RAW_INSTRUCTOR_DATA, INSTRUCTOR_DATA_SOURCE, _TOPICS_BY_TRACK, TRACK_SYLLABUS
    RAW_STUDENTS, DATA_SOURCE = db.fetch_students_data()
    average.sync_topics_from_students(RAW_STUDENTS)
    RAW_INSTRUCTOR_DATA, INSTRUCTOR_DATA_SOURCE = db.fetch_instructor_data()
    _TOPICS_BY_TRACK, TRACK_SYLLABUS = _build_topics_from_instructor_doc(RAW_INSTRUCTOR_DATA)
    STUDENT_ROSTER = _build_student_roster(RAW_STUDENTS)
    RAW_DATA = _build_raw_data_from_students(STUDENT_ROSTER)


def _refresh_single_student_in_roster(register_number: str) -> None:
    """
    Refreshes just ONE student after a targeted write (topic score /
    assignment change), instead of calling reload_students() — which used
    to re-fetch the ENTIRE student collection from MongoDB (a full
    collection round trip) just to reflect a single edited record. This
    does one small `find_one` for the changed student, patches it back
    into the in-memory roster, and rebuilds the aggregate cohort stats
    locally (cheap, no network call) — turning what used to be an
    O(all students) network fetch per edit into O(1).
    """
    global STUDENT_ROSTER, RAW_DATA
    fresh_doc = db.fetch_single_student(register_number)
    if not fresh_doc:
        return

    reg_clean = register_number.strip().lower()
    for i, r in enumerate(RAW_STUDENTS):
        if (r.get("RegNumber") or "").strip().lower() == reg_clean:
            RAW_STUDENTS[i] = fresh_doc
            break
    else:
        RAW_STUDENTS.append(fresh_doc)

    STUDENT_ROSTER = _build_student_roster(RAW_STUDENTS)
    RAW_DATA = _build_raw_data_from_students(STUDENT_ROSTER)

    # Keep MongoDB Atlas TrackStatus consistent with performance-based classification
    refreshed_st = find_roster_student(register_number)
    if refreshed_st:
        db.update_student_status(register_number, refreshed_st["studentStatus"])



# ---------------------------------------------------------------------------
# MODULE SCORES EXTRACTION (Exact from Scores dict)
# ---------------------------------------------------------------------------
AI_TOPIC_NAMES = {
    "API": "API Development",
    "API-Basics": "API Development",
    "APIBasics": "API Development",
    "LLMTokenization": "LLM Tokenization",
    "ChromaDB": "ChromaDB Vector Store",
    "VectorDB": "Vector Databases",
    "APIFoldersSQLNoSQL": "SQL & NoSQL API Integration",
    "API-DB-CRUD-Operations": "SQL & NoSQL API Integration",
    "HuggingFace": "HuggingFace",
    "PromptEngineering": "Prompt Engineering",
}

DEVOPS_TOPIC_NAMES = {
    "Docker": "Docker Fundamentals",
    "DockerInto File": "Docker Fundamentals",
    "DevDockerGit": "DevDockerGit",
    "GitDockerOperations": "Docker Workflow",
    "Appdev Git": "Docker Workflow",
    "NGNIX": "NGNIX",
    "Compose Volume Network NGNIX": "NGNIX",
}


def _extract_student_modules(student: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    raw_scores = student.get("scores") or student.get("raw", {}).get("Scores", {})
    ai_raw = raw_scores.get("AI", {})
    devops_raw = raw_scores.get("DevOps", {})

    # Topics that exist in Scores.[AI|DevOps] only because an assignment was
    # synced in (db.sync_assignment_to_student_record) — NOT genuine
    # assessment scores, even if that assignment happened to carry a score
    # value. A topic is only a real assessment once it's gone through
    # db.add_student_topic_score (which also mirrors into the
    # 'assesement'/'assessment' collections and removes any matching entry
    # here). Matched case-insensitively since topic names are free text.
    assignment_topics_raw = student.get("assignmentTopics") or student.get("raw", {}).get("AssignmentTopics", {}) or {}
    ai_assignment_keys = {str(t).strip().lower() for t in (assignment_topics_raw.get("AI") or [])}
    devops_assignment_keys = {str(t).strip().lower() for t in (assignment_topics_raw.get("DevOps") or [])}

    ai_modules = []
    all_ai_keys = [k for k in ai_raw.keys() if k != "DevOps"]

    for key in all_ai_keys:
        val = ai_raw.get(key)
        label = AI_TOPIC_NAMES.get(key, key)
        is_assignment_topic = key.strip().lower() in ai_assignment_keys
        has_score = (val is not None and str(val).strip() != "") and not is_assignment_topic
        ai_modules.append({
            "module": f"{label} ({key})" if label != key else key,
            "rawKey": key,
            "pct": _format_score(val) if has_score else 0,
            "scoreDisplay": _display_score(val) if has_score else "--",
            # True only for a genuine assessment score — excludes both
            # unscored AND scored assignment-sourced topics.
            "hasScore": has_score,
        })

    devops_modules = []
    all_devops_keys = [k for k in devops_raw.keys() if k != "AI"]

    for key in all_devops_keys:
        val = devops_raw.get(key)
        label = DEVOPS_TOPIC_NAMES.get(key, key)
        is_assignment_topic = key.strip().lower() in devops_assignment_keys
        has_score = (val is not None and str(val).strip() != "") and not is_assignment_topic
        devops_modules.append({
            "module": f"{label} ({key})" if label != key else key,
            "rawKey": key,
            "pct": _format_score(val) if has_score else 0,
            "scoreDisplay": _display_score(val) if has_score else "--",
            "hasScore": has_score,
        })

    # Sort individual module scores high to low (unscored placed after scored)
    ai_modules.sort(key=lambda m: (1 if m.get("hasScore") else 0, m.get("pct", 0)), reverse=True)
    devops_modules.sort(key=lambda m: (1 if m.get("hasScore") else 0, m.get("pct", 0)), reverse=True)

    return ai_modules, devops_modules


# ---------------------------------------------------------------------------
# CATEGORY-WISE STUDENT SELECTION
# ---------------------------------------------------------------------------
def select_category_students(
    students_list: List[Dict[str, Any]],
    category: str,
    score_key: str = "average",
    track_label: str = "All tracks",
) -> List[Dict[str, Any]]:
    """
    Category-wise student selection logic for the Student panel:
    - 🟢 On Track: 5 best-performing students (sorted by score highest to lowest, top 5).
    - 🟡 Moderate: 6 students total (top 3 highest-scoring followed by 3 lowest-scoring).
    - 🔴 Critical: 5 lowest-scoring students (sorted by score lowest to highest, bottom 5).

    The category is determined strictly from the student's actual status for the track score.
    Students are selected only from their respective category without mixing.
    """
    cat_students = []
    for s in students_list:
        val = s.get(score_key)
        score_val = float(val) if val is not None and val != "" else 0.0
        if status_for_score(score_val) == category:
            cat_students.append(s)

    def _format(s: Dict[str, Any]) -> Dict[str, Any]:
        val = s.get(score_key)
        score_val = float(val) if val is not None and val != "" else 0.0
        disp_key = f"{score_key}Display" if score_key != "average" else "averageDisplay"
        disp = s.get(disp_key) or _display_score(score_val)
        student_track = track_label
        if track_label == "All tracks":
            student_track = "DevOps Track" if (s.get("hasDevops") and (s.get("devopsScore") or 0) > (s.get("aiScore") or 0)) else "AI Track"
        return {
            "name": s["name"],
            "registerNumber": s["registerNumber"],
            "track": student_track,
            "score": score_val,
            "scoreDisplay": disp,
            "numericScore": score_val,
            "branch": s.get("branch", ""),
            "mentor": s.get("mentor", ""),
            "lastActive": "today",
            "onWatchlist": s.get("onWatchlist", False),
        }

    if category == "onTrack":
        # 5 best-performing: highest to lowest
        sorted_list = sorted(
            cat_students,
            key=lambda s: (-float(s.get(score_key, 0) or 0), s.get("registerNumber", "")),
        )
        return [_format(s) for s in sorted_list[:5]]

    elif category == "critical":
        # 5 lowest-scoring: lowest to highest
        sorted_list = sorted(
            cat_students,
            key=lambda s: (float(s.get(score_key, 0) or 0), s.get("registerNumber", "")),
        )
        return [_format(s) for s in sorted_list[:5]]

    elif category == "moderate":
        # 6 students total: top 3 highest-scoring followed by 3 lowest-scoring
        if len(cat_students) <= 6:
            sorted_list = sorted(
                cat_students,
                key=lambda s: (-float(s.get(score_key, 0) or 0), s.get("registerNumber", "")),
            )
            return [_format(s) for s in sorted_list]

        # Top 3 highest-scoring Moderate students
        top_3 = sorted(
            cat_students,
            key=lambda s: (-float(s.get(score_key, 0) or 0), s.get("registerNumber", "")),
        )[:3]

        top_3_regs = {s["registerNumber"] for s in top_3}
        remaining = [s for s in cat_students if s["registerNumber"] not in top_3_regs]

        # 3 lowest-scoring Moderate students (lowest first)
        lowest_3 = sorted(
            remaining,
            key=lambda s: (float(s.get(score_key, 0) or 0), s.get("registerNumber", "")),
        )[:3]

        return [_format(s) for s in top_3] + [_format(s) for s in lowest_3]

    return []


def select_panel_students(
    students_list: List[Dict[str, Any]],
    category: str,
    score_key: str = "average",
    track_label: str = "All tracks",
) -> List[Dict[str, Any]]:
    """
    Selects students for the Student panel strictly by their Performance score:
    - 'selected' -> students whose Performance >= 50
    - 'rejected' -> students whose Performance < 50
    The 50% threshold is inclusive. Missing/null performance is not classified as Selected.
    Students are sorted by score highest to lowest.
    """
    norm_cat = category.strip().lower()
    target_status = "Selected" if norm_cat in ("selected", "select") else (
        "Rejected" if norm_cat in ("rejected", "reject") else None
    )

    cat_students = []
    for s in students_list:
        perf = get_student_performance(s) if s.get("hasWeights", True) else None
        st = classify_student_by_performance(perf)
        if target_status and st == target_status:
            cat_students.append(s)

    def _format(s: Dict[str, Any]) -> Dict[str, Any]:
        val = s.get(score_key)
        score_val = float(val) if val is not None and val != "" else 0.0
        disp_key = f"{score_key}Display" if score_key != "average" else "averageDisplay"
        disp = s.get(disp_key) or _display_score(score_val)
        student_track = track_label
        if track_label == "All tracks":
            student_track = "DevOps Track" if (s.get("hasDevops") and (s.get("devopsScore") or 0) > (s.get("aiScore") or 0)) else "AI Track"
        perf = get_student_performance(s) if s.get("hasWeights", True) else None
        return {
            "name": s["name"],
            "registerNumber": s["registerNumber"],
            "track": student_track,
            "score": score_val,
            "scoreDisplay": disp,
            "numericScore": score_val,
            "performance": perf,
            "totalWeightage": s.get("totalWeightage", perf),
            "branch": s.get("branch", ""),
            "mentor": s.get("mentor", ""),
            "lastActive": "today",
            "onWatchlist": s.get("onWatchlist", False),
            "studentStatus": classify_student_by_performance(perf),
        }

    # Sort students by Performance percentage in descending order (highest Performance first)
    def _sort_perf(s: Dict[str, Any]) -> float:
        p = get_student_performance(s) if s.get("hasWeights", True) else None
        return float(p) if p is not None else -1.0

    cat_students.sort(
        key=lambda s: (-_sort_perf(s), s.get("registerNumber", ""))
    )
    return [_format(s) for s in cat_students]


# ---------------------------------------------------------------------------
# ASSESSMENT CHARTS DYNAMIC TOPIC CONFIGURATION & AGGREGATION
# ---------------------------------------------------------------------------
BASE_AI_ASSESSMENT_TOPICS: List[Tuple[str, List[str]]] = [
    ("API Development", ["API-Basics", "API", "APIBasics", "API Development"]),
    ("LLM Tokenization", ["LLMTokenization", "LLM Tokenization"]),
    ("ChromaDB Vector Store", ["ChromaDB", "Chroma DB", "ChromaDB Vector Store"]),
    ("SQL & NoSQL API Integration", ["API-DB-CRUD-Operations", "APIFoldersSQLNoSQL", "SQL & NoSQL API Integration"]),
    ("HuggingFace", ["HuggingFace"]),
    ("Vector Databases", ["VectorDB", "Vector Databases"]),
]

BASE_DEVOPS_ASSESSMENT_TOPICS: List[Tuple[str, List[str]]] = [
    ("Docker Fundamentals", ["DockerInto File", "Docker", "Docker Fundamentals"]),
    ("Docker Workflow", ["Appdev Git", "GitDockerOperations", "Docker Workflow", "Git & Docker Workflow"]),
    ("DevDockerGit", ["DevDockerGit"]),
    ("NGNIX", ["Compose Volume Network NGNIX", "NGNIX"]),
]


def _format_topic_display_name(key: str, track_name: str = "AI") -> str:
    mapping = DEVOPS_TOPIC_NAMES if "devops" in track_name.lower() else AI_TOPIC_NAMES
    if key in mapping:
        return mapping[key]
    norm_key = average.normalize_topic_name(key)
    for k, v in mapping.items():
        if average.normalize_topic_name(k) == norm_key:
            return v
    words = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z][a-z0-9]|\b)", str(key))
    return " ".join(words) if words else str(key).title()


def _build_dynamic_track_assessment(
    track_name: str,
    roster_list: List[Dict[str, Any]],
    base_topics: List[Tuple[str, List[str]]],
) -> List[Dict[str, Any]]:
    result = []
    registered_norms = set()

    for label, aliases in base_topics:
        alias_norms = {average.normalize_topic_name(a) for a in aliases}
        registered_norms.update(alias_norms)
        vals = []
        for s in roster_list:
            sc = s.get("scores", {}).get(track_name, {})
            v = None
            for a in aliases:
                if a in sc and sc[a] is not None and str(sc[a]).strip() != "":
                    v = sc[a]
                    break
            if v is None:
                for sk, sv in sc.items():
                    if average.normalize_topic_name(sk) in alias_norms and sv is not None and str(sv).strip() != "":
                        v = sv
                        break
            if v is not None and str(v).strip() != "":
                try:
                    vals.append(_format_score(v))
                except (ValueError, TypeError):
                    pass
        avg_score = round(sum(vals) / len(vals)) if vals else 0.0
        result.append({"track": label, "pct": avg_score})

    # Discover dynamic topics not in base definitions
    all_keys = []
    if track_name == "AI":
        all_keys.extend(average.get_ai_topics())
    else:
        all_keys.extend(average.get_devops_topics())
    for s in roster_list:
        sc = s.get("scores", {}).get(track_name, {})
        for k in sc.keys():
            if k not in all_keys:
                all_keys.append(k)

    for k in all_keys:
        k_norm = average.normalize_topic_name(k)
        if not k_norm or k_norm in registered_norms or k.lower() in ("ai", "devops"):
            continue
        registered_norms.add(k_norm)
        disp_name = _format_topic_display_name(k, track_name)
        vals = []
        for s in roster_list:
            sc = s.get("scores", {}).get(track_name, {})
            v = sc.get(k)
            if v is None:
                for sk, sv in sc.items():
                    if average.normalize_topic_name(sk) == k_norm and sv is not None and str(sv).strip() != "":
                        v = sv
                        break
            if v is not None and str(v).strip() != "":
                try:
                    vals.append(_format_score(v))
                except (ValueError, TypeError):
                    pass
        avg_score = round(sum(vals) / len(vals)) if vals else 0.0
        result.append({"track": disp_name, "pct": avg_score})

    return result


# ---------------------------------------------------------------------------
# BUILD RAW_DATA FROM REAL COHORT
# ---------------------------------------------------------------------------
def _build_raw_data_from_students(roster: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not roster:
        return {
            "AI Track": {"strength": 0, "score": 0, "watchlist": 0, "risk": 0, "classes": 6, "syllabus": 0, "assessment": [], "warning": {"critical": 0, "moderate": 0, "onTrack": 0}, "attendance": [], "dailyScores": [], "students": {"critical": [], "moderate": [], "onTrack": [], "selected": [], "rejected": []}, "panelCounts": {"selected": 0, "rejected": 0}, "panelStudents": {"selected": [], "rejected": []}},
            "DevOps Track": {"strength": 0, "score": 0, "watchlist": 0, "risk": 0, "classes": 5, "syllabus": 0, "assessment": [], "warning": {"critical": 0, "moderate": 0, "onTrack": 0}, "attendance": [], "dailyScores": [], "students": {"critical": [], "moderate": [], "onTrack": [], "selected": [], "rejected": []}, "panelCounts": {"selected": 0, "rejected": 0}, "panelStudents": {"selected": [], "rejected": []}},
        }

    total_count = len(roster)

    # 1. AI Track calculations
    ai_scores = [s["aiScore"] for s in roster]
    ai_avg = round(sum(ai_scores) / len(ai_scores)) if ai_scores else 0

    ai_critical = [s for s in roster if s["aiScore"] < 50]
    ai_moderate = [s for s in roster if 50 <= s["aiScore"] < 75]
    ai_ontrack = [s for s in roster if s["aiScore"] >= 75]

    ai_assessment = _build_dynamic_track_assessment("AI", roster, BASE_AI_ASSESSMENT_TOPICS)

    # 2. DevOps Track calculations
    do_active_students = [s for s in roster if s.get("hasDevops", False)]
    do_scores = [s["devopsScore"] for s in do_active_students]
    do_avg = round(sum(do_scores) / len(do_scores)) if do_scores else 0

    do_critical = [s for s in do_active_students if s["devopsScore"] < 50]
    do_moderate = [s for s in do_active_students if 50 <= s["devopsScore"] < 75]
    do_ontrack = [s for s in do_active_students if s["devopsScore"] >= 75]

    do_assessment = _build_dynamic_track_assessment("DevOps", do_active_students or roster, BASE_DEVOPS_ASSESSMENT_TOPICS)

    # Attendance sessions (simulated as attendance is blank in dataset)
    ai_attendance = [
        {"date": "2026-08-04", "day": "Tue", "present": round(total_count * 0.92), "total": total_count},
        {"date": "2026-08-06", "day": "Thu", "present": round(total_count * 0.88), "total": total_count},
        {"date": "2026-08-11", "day": "Tue", "present": round(total_count * 0.94), "total": total_count},
        {"date": "2026-08-13", "day": "Thu", "present": round(total_count * 0.85), "total": total_count},
        {"date": "2026-08-18", "day": "Tue", "present": round(total_count * 0.91), "total": total_count},
        {"date": "2026-08-20", "day": "Thu", "present": round(total_count * 0.87), "total": total_count},
        {"date": "2026-08-25", "day": "Tue", "present": round(total_count * 0.95), "total": total_count},
        {"date": "2026-08-27", "day": "Thu", "present": round(total_count * 0.89), "total": total_count},
    ]

    do_total = len(do_active_students) or total_count
    do_attendance = [
        {"date": "2026-08-05", "day": "Wed", "present": round(do_total * 0.90), "total": do_total},
        {"date": "2026-08-07", "day": "Fri", "present": round(do_total * 0.86), "total": do_total},
        {"date": "2026-08-12", "day": "Wed", "present": round(do_total * 0.93), "total": do_total},
        {"date": "2026-08-14", "day": "Fri", "present": round(do_total * 0.84), "total": do_total},
        {"date": "2026-08-19", "day": "Wed", "present": round(do_total * 0.92), "total": do_total},
        {"date": "2026-08-21", "day": "Fri", "present": round(do_total * 0.88), "total": do_total},
        {"date": "2026-08-26", "day": "Wed", "present": round(do_total * 0.94), "total": do_total},
        {"date": "2026-08-28", "day": "Fri", "present": round(do_total * 0.90), "total": do_total},
    ]

    ai_daily = [
        {"date": "2026-08-04", "day": "Tue", "avgScore": clamp(ai_avg - 8, 10, 95)},
        {"date": "2026-08-06", "day": "Thu", "avgScore": clamp(ai_avg - 6, 10, 95)},
        {"date": "2026-08-11", "day": "Tue", "avgScore": clamp(ai_avg - 4, 10, 95)},
        {"date": "2026-08-13", "day": "Thu", "avgScore": clamp(ai_avg - 2, 10, 95)},
        {"date": "2026-08-18", "day": "Tue", "avgScore": clamp(ai_avg - 1, 10, 95)},
        {"date": "2026-08-20", "day": "Thu", "avgScore": clamp(ai_avg + 1, 10, 95)},
        {"date": "2026-08-25", "day": "Tue", "avgScore": clamp(ai_avg + 2, 10, 95)},
        {"date": "2026-08-27", "day": "Thu", "avgScore": ai_avg},
    ]

    do_daily = [
        {"date": "2026-08-05", "day": "Wed", "avgScore": clamp(do_avg - 7, 10, 95)},
        {"date": "2026-08-07", "day": "Fri", "avgScore": clamp(do_avg - 5, 10, 95)},
        {"date": "2026-08-12", "day": "Wed", "avgScore": clamp(do_avg - 3, 10, 95)},
        {"date": "2026-08-14", "day": "Fri", "avgScore": clamp(do_avg - 1, 10, 95)},
        {"date": "2026-08-19", "day": "Wed", "avgScore": clamp(do_avg, 10, 95)},
        {"date": "2026-08-21", "day": "Fri", "avgScore": clamp(do_avg + 1, 10, 95)},
        {"date": "2026-08-26", "day": "Wed", "avgScore": clamp(do_avg + 2, 10, 95)},
        {"date": "2026-08-28", "day": "Fri", "avgScore": do_avg},
    ]

    ai_watchlist = [s for s in roster if s.get("onWatchlist")]
    do_watchlist = [s for s in do_active_students if s.get("onWatchlist")]

    ai_selected = select_panel_students(roster, "selected", "aiScore", "AI Track")
    ai_rejected = select_panel_students(roster, "rejected", "aiScore", "AI Track")

    do_selected = select_panel_students(do_active_students, "selected", "devopsScore", "DevOps Track")
    do_rejected = select_panel_students(do_active_students, "rejected", "devopsScore", "DevOps Track")

    return {
        "AI Track": {
            "strength": total_count,
            "score": ai_avg,
            "watchlist": len(ai_watchlist),
            "risk": len(ai_critical),
            "classes": 6,
            "syllabus": TRACK_SYLLABUS.get("AI Track", 58),
            "assessment": ai_assessment,
            "warning": {"critical": len(ai_critical), "moderate": len(ai_moderate), "onTrack": len(ai_ontrack)},
            "attendance": ai_attendance,
            "dailyScores": ai_daily,
            "panelCounts": {"selected": len(ai_selected), "rejected": len(ai_rejected)},
            "panelStudents": {"selected": ai_selected, "rejected": ai_rejected},
            "students": {
                "critical": select_category_students(roster, "critical", "aiScore", "AI Track"),
                "moderate": select_category_students(roster, "moderate", "aiScore", "AI Track"),
                "onTrack": select_category_students(roster, "onTrack", "aiScore", "AI Track"),
                "selected": ai_selected,
                "rejected": ai_rejected,
            },
        },
        "DevOps Track": {
            "strength": len(do_active_students),
            "score": do_avg,
            "watchlist": len(do_watchlist),
            "risk": len(do_critical),
            "classes": 5,
            "syllabus": TRACK_SYLLABUS.get("DevOps Track", 74),
            "assessment": do_assessment,
            "warning": {"critical": len(do_critical), "moderate": len(do_moderate), "onTrack": len(do_ontrack)},
            "attendance": do_attendance,
            "dailyScores": do_daily,
            "panelCounts": {"selected": len(do_selected), "rejected": len(do_rejected)},
            "panelStudents": {"selected": do_selected, "rejected": do_rejected},
            "students": {
                "critical": select_category_students(do_active_students, "critical", "devopsScore", "DevOps Track"),
                "moderate": select_category_students(do_active_students, "moderate", "devopsScore", "DevOps Track"),
                "onTrack": select_category_students(do_active_students, "onTrack", "devopsScore", "DevOps Track"),
                "selected": do_selected,
                "rejected": do_rejected,
            },
        },
    }


# ---------------------------------------------------------------------------
# INSTRUCTOR SYLLABUS & TOPICS DATA
# ---------------------------------------------------------------------------
def _normalize_topic_status(s: Optional[str]) -> str:
    cleaned = (s or "").strip().lower().replace("_", " ")
    if cleaned in ("completed", "complete"):
        return "Completed"
    elif cleaned in ("in progress", "inprogress", "ongoing"):
        return "In progress"
    elif cleaned in ("upcoming", "todo"):
        return "Upcoming"
    return (s or "Upcoming").capitalize()


def _build_topics_from_instructor_doc(doc: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, int]]:
    by_track: Dict[str, List[Dict[str, Any]]] = {"AI Track": [], "DevOps Track": []}
    syllabus: Dict[str, int] = {"AI Track": 58, "DevOps Track": 74}

    if doc:
        ai_data = doc.get("ai_track", {})
        if "syllabus_percent" in ai_data:
            syllabus["AI Track"] = int(ai_data["syllabus_percent"])
        for t in ai_data.get("topics", []):
            by_track["AI Track"].append({
                "topic": t.get("topic", ""),
                "track": "AI Track",
                "status": _normalize_topic_status(t.get("status", "Upcoming")),
                "date": t.get("date_completed") or t.get("date"),
            })

        devops_data = doc.get("devops_track", {})
        if "syllabus_percent" in devops_data:
            syllabus["DevOps Track"] = int(devops_data["syllabus_percent"])
        for t in devops_data.get("topics", []):
            by_track["DevOps Track"].append({
                "topic": t.get("topic", ""),
                "track": "DevOps Track",
                "status": _normalize_topic_status(t.get("status", "Upcoming")),
                "date": t.get("date_completed") or t.get("date"),
            })

    if not by_track["AI Track"]:
        by_track["AI Track"] = [
            {"topic": "API Development", "date": "2026-08-06", "status": "Completed", "track": "AI Track"},
            {"topic": "LLM Tokenization", "date": "2026-08-11", "status": "Completed", "track": "AI Track"},
            {"topic": "ChromaDB Vector Store", "date": "2026-08-18", "status": "Completed", "track": "AI Track"},
            {"topic": "SQL & NoSQL API Integration", "date": "2026-08-25", "status": "In progress", "track": "AI Track"},
            {"topic": "Autonomous Agents", "date": None, "status": "Upcoming", "track": "AI Track"},
        ]
    if not by_track["DevOps Track"]:
        by_track["DevOps Track"] = [
            {"topic": "Docker Fundamentals", "date": "2026-08-07", "status": "Completed", "track": "DevOps Track"},
            {"topic": "Git & Docker Workflow", "date": "2026-08-14", "status": "Completed", "track": "DevOps Track"},
            {"topic": "CI/CD Pipeline Automation", "date": "2026-08-21", "status": "Completed", "track": "DevOps Track"},
            {"topic": "Kubernetes Orchestration", "date": "2026-08-26", "status": "In progress", "track": "DevOps Track"},
            {"topic": "Cloud Infrastructure & Monitoring", "date": None, "status": "Upcoming", "track": "DevOps Track"},
        ]

    return by_track, syllabus


RAW_INSTRUCTOR_DATA, INSTRUCTOR_DATA_SOURCE = db.fetch_instructor_data()
_TOPICS_BY_TRACK, TRACK_SYLLABUS = _build_topics_from_instructor_doc(RAW_INSTRUCTOR_DATA)

RAW_DATA: Dict[str, Dict[str, Any]] = _build_raw_data_from_students(STUDENT_ROSTER)


def add_topic(topic: str, track: str, status: str, date: Optional[str]) -> Dict[str, Any]:
    norm_status = _normalize_topic_status(status)
    new_topic = {"topic": topic, "track": track, "status": norm_status, "date": date}
    _TOPICS_BY_TRACK.setdefault(track, []).append(new_topic)
    db.add_instructor_topic(topic, track, norm_status, date)
    return new_topic


def update_topic_status(topic: str, track: str, status: str, date: Optional[str] = None) -> bool:
    norm_status = _normalize_topic_status(status)
    matched = False
    target_tracks = [track] if track in _TOPICS_BY_TRACK else ["AI Track", "DevOps Track"]
    for t_track in target_tracks:
        for t in _TOPICS_BY_TRACK[t_track]:
            if t["topic"].strip().lower() == topic.strip().lower():
                t["status"] = norm_status
                if date is not None:
                    t["date"] = date
                elif norm_status == "Completed" and not t.get("date"):
                    from datetime import date as dt_date
                    t["date"] = dt_date.today().isoformat()
                elif norm_status == "Upcoming":
                    t["date"] = None
                matched = True
                break
        if matched:
            break

    db.update_instructor_topic_status(topic, track, norm_status, date)
    return matched


def get_topics(track_filter: str) -> List[Dict[str, Any]]:
    if track_filter != "All tracks":
        return list(_TOPICS_BY_TRACK.get(track_filter, []))
    combined = [*_TOPICS_BY_TRACK.get("AI Track", []), *_TOPICS_BY_TRACK.get("DevOps Track", [])]
    combined.sort(key=lambda t: (t["date"] is None, t["date"] or ""))
    return combined


# ---------------------------------------------------------------------------
# FINDERS & PER-STUDENT DRILL-DOWNS
# ---------------------------------------------------------------------------
def find_roster_student(register_number: str) -> Optional[Dict[str, Any]]:
    for s in STUDENT_ROSTER:
        if s["registerNumber"] == register_number:
            return s
    return None


def find_admin_student(register_number: str) -> Optional[Dict[str, Any]]:
    for s in STUDENT_ROSTER:
        if s["registerNumber"] == register_number:
            return {
                "name": s["name"],
                "registerNumber": s["registerNumber"],
                "track": "AI Track" if s["aiScore"] >= (s.get("devopsScore") or 0) else "DevOps Track",
                "score": s["average"],
                "scoreDisplay": s["averageDisplay"],
                "numericScore": s["average"],
                "mentor": s["mentor"],
                "branch": s["branch"],
                "lastActive": "today",
                "onWatchlist": s.get("onWatchlist", False),
            }
    return None


def admin_student_to_roster_shape(student: Dict[str, Any]) -> Dict[str, Any]:
    matched = find_roster_student(student["registerNumber"])
    if matched:
        return matched

    return {
        "registerNumber": student["registerNumber"],
        "name": student["name"],
        "email": f"{student['registerNumber'].lower()}@student.edu",
        "mentor": student.get("mentor", "A. Sharma"),
        "aiScore": student.get("numericScore", 70),
        "devopsScore": student.get("numericScore", 70),
        "average": student.get("numericScore", 70),
        "aiScoreDisplay": str(student.get("numericScore", 70)) + "%",
        "devopsScoreDisplay": str(student.get("numericScore", 70)) + "%",
        "averageDisplay": str(student.get("numericScore", 70)) + "%",
        "branch": student.get("branch", ""),
        "onWatchlist": student.get("onWatchlist", False),
        "scores": {},
    }


def build_roster_student_detail(student: Dict[str, Any]) -> Dict[str, Any]:
    average = student.get("average", 70.0)

    ai_modules, devops_modules = _extract_student_modules(student)
    all_modules = ai_modules + devops_modules

    weakest = min(all_modules, key=lambda m: m["pct"]) if all_modules else {"module": "Modules", "pct": 0}
    strongest = max(all_modules, key=lambda m: m["pct"]) if all_modules else {"module": "Modules", "pct": 0}

    # Trend chart displays the student's exact sequential scores across their
    # modules — but ONLY real assessment scores. Assignment topics that were
    # synced into Scores.[AI|DevOps] before being scored (hasScore=False)
    # must not appear here as fake 0% data points.
    scored_modules = [m for m in all_modules if m.get("hasScore") is True]
    score_trend = []
    for m in scored_modules:
        score_trend.append({"week": m["rawKey"], "score": m["pct"]})

    # Attendance rate (simulated based on performance)
    attendance_rate = int(clamp(round(average * 0.5 + 45), 50, 100))

    # Assignments completed/total must come from the actual `assignments`
    # collection (real assigned work), NOT from the student's full list of
    # topic/assessment scores (`all_modules`) — those are two different things.
    try:
        assignment_records = get_student_assignments(student.get("registerNumber", ""))
    except Exception:
        assignment_records = []
    total_assignments = len(assignment_records)
    assignments_done = sum(
        1 for a in assignment_records
        if (a.get("status") or "").strip().upper() == "COMPLETED" or a.get("score") is not None
    )
    streak = 3 if average >= 75 else 2 if average >= 50 else 1

    weights = [
        {"assessmentType": w.get("AssessmentType", ""), "weightage": w.get("Weightage", 0)}
        for w in (student.get("weights") or [])
        if isinstance(w, dict)
    ]
    # Weights are shown exactly as stored in Mongo — no baseline topics are
    # injected any more.

    status = status_for_score(average)
    feedback_notes = {
        "critical": f"{student['name']} requires immediate intervention. Weakest assessment in {weakest['module']} ({weakest['pct']}). Schedule a 1:1 mentor sync this week to review foundational concepts.",
        "moderate": f"{student['name']} is making steady progress, demonstrating strong grasp of {strongest['module']} ({strongest['pct']}). Target {weakest['module']} ({weakest['pct']}) for targeted revision.",
        "onTrack": f"Excellent performance! {student['name']} leads with {strongest['module']} ({strongest['pct']}). Recommend advancing to challenge projects and mentor code reviews.",
    }

    # A mentor-edited note (saved via /api/student/feedback, stored under
    # MentorFeedback in the students collection) always takes priority over
    # the auto-generated one above. Students who haven't had feedback
    # edited yet fall back to the auto text so the card is never empty.
    custom_feedback = student.get("mentorFeedback")
    if custom_feedback and (custom_feedback.get("Note") or "").strip():
        feedback = {
            "from": custom_feedback.get("From") or student.get("mentor", "Mentor Team"),
            "date": custom_feedback.get("Date") or "official assessment",
            "note": custom_feedback["Note"],
            "isCustom": True,
        }
    else:
        feedback = {
            "from": student.get("mentor", "Mentor Team"),
            "date": "official assessment",
            "note": feedback_notes.get(status, feedback_notes["moderate"]),
            "isCustom": False,
        }

    return {
        "status": status,
        "average": average,
        "onWatchlist": student.get("onWatchlist", False),
        "aiModules": ai_modules,
        "devopsModules": devops_modules,
        "scoreTrend": score_trend,
        "attendanceRate": attendance_rate,
        "assignmentsDone": assignments_done,
        "totalAssignments": total_assignments,
        "streak": streak,
        "weights": weights,
        "feedback": feedback,
    }


# ---------------------------------------------------------------------------
# WATCHLIST MANAGEMENT
# ---------------------------------------------------------------------------
def get_watchlist_students(track_filter: str = "All tracks") -> List[Dict[str, Any]]:
    """Returns the list of students currently added to the watchlist."""
    active = [s for s in STUDENT_ROSTER if s.get("onWatchlist")]
    if track_filter == "DevOps Track":
        active = [s for s in active if s.get("hasDevops")]

    return [
        {
            "name": s["name"],
            "registerNumber": s["registerNumber"],
            "track": "AI Track" if s["aiScore"] >= (s.get("devopsScore") or 0) else "DevOps Track",
            "score": s["average"],
            "scoreDisplay": s["averageDisplay"],
            "numericScore": s["average"],
            "branch": s["branch"],
            "mentor": s["mentor"],
            "mobile": s.get("mobile", ""),
            "email": s.get("email", ""),
            "onWatchlist": True,
        }
        for s in sorted(active, key=lambda x: x["average"])
    ]


def get_at_risk_students(track_filter: str = "All tracks") -> List[Dict[str, Any]]:
    """Returns all students whose performance score is in the critical risk range (< 50)."""
    results = []
    for s in STUDENT_ROSTER:
        if track_filter == "AI Track":
            score = s["aiScore"]
            score_disp = s["aiScoreDisplay"]
            track_name = "AI Track"
        elif track_filter == "DevOps Track":
            if not s.get("hasDevops"):
                continue
            score = s["devopsScore"]
            score_disp = s["devopsScoreDisplay"]
            track_name = "DevOps Track"
        else:
            score = s["average"]
            score_disp = s["averageDisplay"]
            track_name = "AI Track" if s["aiScore"] >= (s.get("devopsScore") or 0) else "DevOps Track"

        if score < 50:
            ai_mods, do_mods = _extract_student_modules(s)
            relevant_mods = ai_mods if track_filter == "AI Track" else (do_mods if track_filter == "DevOps Track" else (ai_mods + do_mods))
            weakest = min(relevant_mods, key=lambda m: m["pct"]) if relevant_mods else None
            weakest_str = f"{weakest['module'].split(' (')[0]}: {weakest['pct']}" if weakest else "—"

            results.append({
                "name": s["name"],
                "registerNumber": s["registerNumber"],
                "branch": s["branch"],
                "track": track_name,
                "score": score,
                "scoreDisplay": score_disp,
                "weakestModule": weakest_str,
                "mentor": s["mentor"],
                "mobile": s.get("mobile", ""),
                "email": s.get("email", ""),
                "onWatchlist": s.get("onWatchlist", False),
            })

    results.sort(key=lambda x: x["score"])
    return results



def set_student_watchlist(register_number: str, on_watchlist: bool) -> bool:
    """
    Sets watchlist status for a student, updating in-memory models,
    MongoDB Atlas, and the local JSON file.
    Only WatchListStatus is updated; TrackStatus is untouched.
    """
    global RAW_DATA
    matched = False
    reg_clean = register_number.strip().lower()
    for s in STUDENT_ROSTER:
        if s["registerNumber"].strip().lower() == reg_clean:
            s["onWatchlist"] = on_watchlist
            s["watchListStatus"] = "Watchlist" if on_watchlist else ""
            if "raw" in s:
                s["raw"]["WatchListStatus"] = "Watchlist" if on_watchlist else ""
            matched = True
            break

    for r in RAW_STUDENTS:
        if r.get("RegNumber", "").strip().lower() == reg_clean:
            r["WatchListStatus"] = "Watchlist" if on_watchlist else ""
            break

    # Rebuild RAW_DATA so Admin counts and lists update immediately
    RAW_DATA = _build_raw_data_from_students(STUDENT_ROSTER)

    # Persist in MongoDB Atlas & local JSON
    db.update_student_watchlist(register_number, on_watchlist)
    return matched


def set_student_status(register_number: str, status: str) -> bool:
    """
    Sets student status ('Selected' or 'Rejected'), updates in-memory models,
    and persists directly to MongoDB Atlas.
    """
    global RAW_DATA
    norm_status = "Selected" if str(status).strip().lower() in ("selected", "select") else (
        "Rejected" if str(status).strip().lower() in ("rejected", "reject") else str(status).strip()
    )
    matched = False
    reg_clean = register_number.strip().lower()
    for s in STUDENT_ROSTER:
        if s["registerNumber"].strip().lower() == reg_clean:
            s["studentStatus"] = norm_status
            s["trackStatus"] = norm_status
            if "raw" in s:
                s["raw"]["TrackStatus"] = norm_status
            matched = True
            break

    for r in RAW_STUDENTS:
        if r.get("RegNumber", "").strip().lower() == reg_clean:
            r["TrackStatus"] = norm_status
            break

    # Rebuild RAW_DATA so Admin counts and lists update immediately
    RAW_DATA = _build_raw_data_from_students(STUDENT_ROSTER)

    # Persist in MongoDB Atlas
    db.update_student_status(register_number, norm_status)
    return matched


def update_student_scores(
    register_number: str,
    ai_score: Optional[float] = None,
    devops_score: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Updates a student's track-level AI Score and/or DevOps Score, recalculates the
    aggregate average score using the calculation logic from average.py, persists
    to MongoDB Atlas, and updates the in-memory roster.
    Returns (student, detail).
    """
    student = find_roster_student(register_number)
    if not student:
        raise ValueError(f"Student with register number '{register_number}' not found")

    # Use existing score if not provided in payload
    final_ai = ai_score if ai_score is not None else student.get("aiScore")
    final_devops = devops_score if devops_score is not None else (student.get("devopsScore") if student.get("hasDevops") else None)

    # Re-calculate averages using average.py
    calc_res = average.calculate_student_averages_from_tracks(final_ai, final_devops)
    agg_score = calc_res["Aggregate"]
    calc_ai = calc_res["AI"]
    calc_devops = calc_res["DevOps"]

    # Persist to MongoDB Atlas
    db.update_student_track_scores(register_number, calc_ai, calc_devops, agg_score)

    # Refresh in-memory student cache
    _refresh_single_student_in_roster(register_number)
    updated_student = find_roster_student(register_number)
    if not updated_student:
        raise ValueError(f"Student {register_number} not found after updating scores")

    detail = build_roster_student_detail(updated_student)
    return updated_student, detail


def add_topic_score_for_student(register_number: str, track: str, topic: str, score: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Adds/updates topic score for a student, persists changes in db/JSON, and reloads in-memory models.
    Returns (student, detail).
    """
    db.add_student_topic_score(register_number, track, topic, score)
    _refresh_single_student_in_roster(register_number)
    student = find_roster_student(register_number)
    if not student:
        raise ValueError(f"Student {register_number} not found after adding score")
    detail = build_roster_student_detail(student)
    return student, detail


def update_topic_score_for_student(
    register_number: str,
    old_track: str,
    old_topic: str,
    new_track: str,
    new_topic: str,
    new_score: float,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Updates an existing topic score for a student, persists changes, and reloads in-memory models.
    Returns (student, detail).
    """
    db.update_student_topic_score(register_number, old_track, old_topic, new_track, new_topic, new_score)
    _refresh_single_student_in_roster(register_number)
    student = find_roster_student(register_number)
    if not student:
        raise ValueError(f"Student {register_number} not found after updating score")
    detail = build_roster_student_detail(student)
    return student, detail


def delete_topic_score_for_student(register_number: str, track: str, topic: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Deletes a topic score for a student, persists changes, and reloads in-memory models.
    Returns (student, detail).
    """
    db.delete_student_topic_score(register_number, track, topic)
    _refresh_single_student_in_roster(register_number)
    student = find_roster_student(register_number)
    if not student:
        raise ValueError(f"Student {register_number} not found after deleting score")
    detail = build_roster_student_detail(student)
    return student, detail


def create_exam_for_all_students(topic_name: str, track: str) -> Dict[str, Any]:
    """
    Creates a new exam/topic across ALL students in the students MongoDB collection.
    Initializes each student's score for the topic to None (unassigned).
    Recalculates track averages and aggregate averages across all students.
    Refreshes in-memory cohort data and syllabus.
    """
    success, message, count = db.add_exam_to_all_students(topic_name, track)
    if not success:
        raise ValueError(message)

    # Register human-readable display label
    track_key = "DevOps" if "devops" in track.lower() else "AI"
    topic_key = message
    if track_key == "AI":
        AI_TOPIC_NAMES[topic_key] = topic_name.strip()
    else:
        DEVOPS_TOPIC_NAMES[topic_key] = topic_name.strip()

    reload_students()
    return {
        "status": "success",
        "topicKey": topic_key,
        "topicName": topic_name.strip(),
        "track": track_key,
        "totalStudentsUpdated": count,
        "aiTopicCount": average.get_ai_topic_count(),
        "devopsTopicCount": average.get_devops_topic_count(),
    }


def add_weight_for_student(register_number: str, assessment_type: str, weightage: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Adds/updates an assessment-type weightage for a student, persists changes
    in Mongo (the `students` collection), and reloads in-memory models.
    Returns (student, detail). Raises if the Mongo write did not actually
    succeed, so callers never get a false "success" when nothing was saved.
    """
    saved = db.add_student_weight(register_number, assessment_type, weightage)
    if not saved:
        raise RuntimeError(
            f"Could not save weightage '{assessment_type}' for {register_number} to the students collection"
        )
    _refresh_single_student_in_roster(register_number)
    student = find_roster_student(register_number)
    if not student:
        raise ValueError(f"Student {register_number} not found after adding weightage")
    detail = build_roster_student_detail(student)
    return student, detail


def delete_weight_for_student(register_number: str, assessment_type: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Deletes an assessment-type weightage for a student, persists changes in
    Mongo (the `students` collection), and reloads in-memory models.
    Returns (student, detail). Raises if the Mongo write did not actually
    succeed, so callers never get a false "success" when nothing was saved.
    """
    saved = db.delete_student_weight(register_number, assessment_type)
    if not saved:
        raise RuntimeError(
            f"Could not delete weightage '{assessment_type}' for {register_number} from the students collection"
        )
    _refresh_single_student_in_roster(register_number)
    student = find_roster_student(register_number)
    if not student:
        raise ValueError(f"Student {register_number} not found after deleting weightage")
    detail = build_roster_student_detail(student)
    return student, detail


def get_student_weights_summary(register_number: str) -> Optional[Dict[str, Any]]:
    """
    Live-reads a student's weights from Mongo (NOT the in-memory roster) and
    returns {"weights": [...], "totalWeightage": float}. Entries are
    normalised, de-duplicated by assessment type (case-insensitive, first
    wins — same match rule the write path uses), and non-numeric values are
    skipped. Returns None if the student is missing or the DB call failed.
    """
    raw = db.fetch_student_weights(register_number)
    if raw is None:
        return None

    weights = _normalize_weights(raw)
    total = round(sum(w["weightage"] for w in weights), 2)
    return {"weights": weights, "totalWeightage": total}


def update_feedback_for_student(
    register_number: str, note: str, feedback_from: Optional[str] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Sets/overwrites the mentor feedback note for a student, persists it in
    Mongo (the `students` collection, under MentorFeedback), and reloads
    in-memory models. Returns (student, detail). Raises if the Mongo write
    did not actually succeed.
    """
    from datetime import date as _date

    saved = db.update_student_feedback(register_number, note, feedback_from, _date.today().isoformat())
    if not saved:
        raise RuntimeError(f"Could not save mentor feedback for {register_number} to the students collection")
    _refresh_single_student_in_roster(register_number)
    student = find_roster_student(register_number)
    if not student:
        raise ValueError(f"Student {register_number} not found after updating feedback")
    detail = build_roster_student_detail(student)
    return student, detail


def update_assignment_for_student(
    register_number: str,
    assignment_id: Optional[str],
    old_track: str,
    old_topic: str,
    new_track: str,
    new_topic: str,
    new_score: Optional[float] = None,
    new_status: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Updates an assignment record (topic/track/score/status), persists via
    db.update_assignment_in_mongo (which keeps it tagged as an assignment,
    not a genuine assessment), and reloads in-memory models.
    Returns (student, detail).
    """
    db.update_assignment_in_mongo(
        register_number, assignment_id, old_track, old_topic, new_track, new_topic, new_score, new_status
    )
    _refresh_single_student_in_roster(register_number)
    student = find_roster_student(register_number)
    if not student:
        raise ValueError(f"Student {register_number} not found after updating assignment")
    detail = build_roster_student_detail(student)
    return student, detail


def delete_assignment_for_student(
    register_number: str, assignment_id: Optional[str], track: str, topic: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Deletes an assignment record and its synced Scores/AssignmentTopics entry,
    persists changes, and reloads in-memory models.
    Returns (student, detail).
    """
    db.delete_assignment_in_mongo(register_number, assignment_id, track, topic)
    _refresh_single_student_in_roster(register_number)
    student = find_roster_student(register_number)
    if not student:
        raise ValueError(f"Student {register_number} not found after deleting assignment")
    detail = build_roster_student_detail(student)
    return student, detail



# ---------------------------------------------------------------------------
# ALL TRACKS AGGREGATION & STATS
# ---------------------------------------------------------------------------
def _build_all_tracks_data() -> Dict[str, Any]:
    ai = RAW_DATA["AI Track"]
    devops = RAW_DATA["DevOps Track"]

    # All tracks warnings are based on Aggregate scores
    agg_scores = [s["average"] for s in STUDENT_ROSTER]
    crit_count = sum(1 for s in agg_scores if s < 50)
    mod_count = sum(1 for s in agg_scores if 50 <= s < 75)
    ontrack_count = sum(1 for s in agg_scores if s >= 75)
    warning = {
        "critical": crit_count,
        "moderate": mod_count,
        "onTrack": ontrack_count,
    }

    attendance = ai["attendance"]
    daily_scores = ai["dailyScores"]

    # Watchlist students are all students actually on the watchlist
    watchlist_students = get_watchlist_students("All tracks")

    agg_avg = round(sum(agg_scores) / len(agg_scores)) if agg_scores else 60

    all_selected = select_panel_students(STUDENT_ROSTER, "selected", "average", "All tracks")
    all_rejected = select_panel_students(STUDENT_ROSTER, "rejected", "average", "All tracks")

    return {
        "strength": len(STUDENT_ROSTER),
        "score": agg_avg,
        "watchlist": len(watchlist_students),
        "risk": crit_count,
        "classes": ai["classes"] + devops["classes"],
        "syllabus": round((ai["syllabus"] + devops["syllabus"]) / 2),
        "assessment": [*ai["assessment"], *devops["assessment"]],
        "warning": warning,
        "attendance": attendance,
        "dailyScores": daily_scores,
        "watchlistStudents": watchlist_students,
        "panelCounts": {"selected": len(all_selected), "rejected": len(all_rejected)},
        "panelStudents": {"selected": all_selected, "rejected": all_rejected},
        "students": {
            "critical": select_category_students(STUDENT_ROSTER, "critical", "average", "All tracks"),
            "moderate": select_category_students(STUDENT_ROSTER, "moderate", "average", "All tracks"),
            "onTrack": select_category_students(STUDENT_ROSTER, "onTrack", "average", "All tracks"),
            "selected": all_selected,
            "rejected": all_rejected,
        },
    }


def get_track_data(track_filter: str) -> Dict[str, Any]:
    if track_filter == "All tracks":
        return _build_all_tracks_data()
    data = copy.deepcopy(RAW_DATA.get(track_filter, RAW_DATA["AI Track"]))
    track_wl = get_watchlist_students(track_filter)
    data["watchlist"] = len(track_wl)
    data["watchlistStudents"] = track_wl
    return data


def build_stats(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"key": "strength", "label": "Average class strength", "value": str(d["strength"]), "unit": "students", "icon": "Users", "clickable": True, "hint": "View attendance details"},
        {"key": "score", "label": "Average score", "value": str(d["score"]), "unit": "", "icon": "TrendingUp", "fill": d["score"], "clickable": True, "hint": "View daily score trend"},
        {"key": "watchlist", "label": "Students on watchlist", "value": str(d["watchlist"]), "unit": "students", "icon": "Eye", "tone": "amber", "clickable": True, "hint": "View students on watchlist"},
        {"key": "risk", "label": "Students at risk", "value": str(d["risk"]), "unit": "students", "icon": "AlertTriangle", "tone": "coral", "clickable": True, "hint": "View at-risk students (< 50)"},
        {"key": "classes", "label": "Classes assigned", "value": str(d["classes"]), "unit": "classes", "icon": "ClipboardList"},
        {"key": "syllabus", "label": "Syllabus completion rate", "value": str(d["syllabus"]), "unit": "%", "icon": "CheckCircle2", "fill": d["syllabus"]},
    ]


def format_session_date(iso: str) -> str:
    from datetime import datetime
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.day} {d.strftime('%b')}" if hasattr(d, "strftime") else iso


def format_topic_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    iso_clean = str(iso).strip()
    if iso_clean.startswith("0001"):
        return "Aug 28, 2026"
    try:
        from datetime import datetime
        d = datetime.strptime(iso_clean, "%Y-%m-%d")
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    except Exception:
        return iso_clean


# ---------------------------------------------------------------------------
# LOGGED IN STUDENT VIEW
# ---------------------------------------------------------------------------
STUDENT_PROFILE: Dict[str, Any] = {
    "name": STUDENT_ROSTER[0]["name"] if STUDENT_ROSTER else "Student",
    "track": "AI Track",
    "module": "API Development",
    "mentor": STUDENT_ROSTER[0]["mentor"] if STUDENT_ROSTER else "A. Sharma",
    "status": STUDENT_ROSTER[0]["status"] if STUDENT_ROSTER else "onTrack",
    "cohortRank": 1,
    "cohortSize": len(STUDENT_ROSTER),
}

STUDENT_STATS: List[Dict[str, Any]] = [
    {"key": "score", "label": "My average score", "value": str(STUDENT_ROSTER[0]["averageDisplay"]) if STUDENT_ROSTER else "70%", "unit": "", "icon": "TrendingUp", "fill": STUDENT_ROSTER[0]["average"] if STUDENT_ROSTER else 70},
    {"key": "syllabus", "label": "Syllabus completion", "value": "75", "unit": "%", "icon": "CheckCircle2", "fill": 75},
    {"key": "assignments", "label": "Assignments completed", "value": "14", "unit": "of 18", "icon": "ClipboardCheck"},
    {"key": "attendance", "label": "Attendance rate", "value": "90", "unit": "%", "icon": "Calendar"},
    {"key": "streak", "label": "Current streak", "value": "4", "unit": "days active", "icon": "Flame"},
]

SCORE_TREND: List[Dict[str, Any]] = [
    {"week": "API", "score": 100},
    {"week": "LLMToken", "score": 92},
    {"week": "ChromaDB", "score": 50},
    {"week": "SQLNoSQL", "score": 31},
    {"week": "Docker", "score": 93},
    {"week": "DevDocker", "score": 73},
]

MODULE_SCORES: List[Dict[str, Any]] = [
    {"module": "API Development", "pct": 100},
    {"module": "Docker Fundamentals", "pct": 93},
    {"module": "LLM Tokenization", "pct": 92},
    {"module": "ChromaDB Vector Store", "pct": 50},
    {"module": "SQL & NoSQL API", "pct": 31},
]

UPCOMING_DEADLINES: List[Dict[str, Any]] = [
    {"title": "Vector database querying lab", "due": "Due in 2 days"},
    {"title": "Docker multi-stage build assignment", "due": "Due in 4 days"},
    {"title": "Capstone checkpoint 1", "due": "Due in 6 days"},
]

MENTOR_FEEDBACK: Dict[str, Any] = {
    "from": STUDENT_ROSTER[0]["mentor"] if STUDENT_ROSTER else "A. Sharma",
    "date": "recently",
    "note": "Great performance on API and Docker assignments. Review ChromaDB vector indexing before the capstone checkpoint.",
}


# ---------------------------------------------------------------------------
# ASSIGNMENT MANAGEMENT DATA LAYER
# ---------------------------------------------------------------------------
def get_student_assignments(register_number: str, track: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetches assignments for a specific student identified by register number.
    Optionally filters by track (AI / DevOps).
    """
    items = db.fetch_student_assignments_from_mongo(register_number)
    if track and track.lower() != "all":
        t_clean = "devops" if "devops" in track.lower() else "ai"
        items = [i for i in items if (i.get("track") or "").lower() == t_clean]
    return items


def create_assignments(assignments_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Validates, formats, and persists new assignment records to MongoDB / local fallback.
    """
    import uuid
    from datetime import datetime

    created_items = []
    for item in assignments_data:
        topic = item.get("topic", "").strip()
        track = item.get("track", "AI").strip()
        reg_no = item.get("studentRegisterNumber") or item.get("regNo") or ""
        reg_no = reg_no.strip()

        if not topic:
            raise ValueError("Topic name cannot be empty")
        if track.upper() not in ("AI", "DEVOPS"):
            raise ValueError(f"Invalid track '{track}'. Track must be 'AI' or 'DevOps'.")
        if not reg_no:
            raise ValueError("Student register number cannot be empty")

        asg_id = item.get("assignmentId") or f"ASG-{uuid.uuid4().hex[:8].upper()}"
        created_at = item.get("createdAt") or datetime.utcnow().isoformat()
        status = item.get("status") or "PENDING"
        score = item.get("score")

        doc = {
            "assignmentId": asg_id,
            "topic": topic,
            "track": "DevOps" if track.upper() == "DEVOPS" else "AI",
            "studentRegisterNumber": reg_no,
            "status": status,
            "score": score,
            "createdAt": created_at,
        }
        created_items.append(doc)

    db.save_assignments_to_mongo(created_items)
    return created_items
'''
test_data = [{'Assignment': [{'assignmentId': 'ASG-479D8CB1', 'createdAt': '2026-09-11T03:21:19.591687', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS047', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-427E897C', 'createdAt': '2026-09-11T03:23:26.975214', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS047', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-44120107', 'createdAt': '2026-09-11T03:25:23.817104', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS047', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 68.6, 'Aggregate': 72.38, 'DevOps': 78.67}, 'Branch': 'CS', 'MailID': 'nayanank2005@gmail.com', 'MobileNumber': '9902930360', 'RegNumber': 'DDAICS047', 'Scores': {'AI': {'API': 100, 'APIFoldersSQLNoSQL': 31, 'Amazon Backend': None, 'ChromaDB': 50, 'HuggingFace': 70, 'LLMTokenization': 92, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 73, 'Docker': 93, 'NGNIX': 70}}, 'StudentName': 'Nayana N K', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-A617511B', 'createdAt': '2026-09-11T03:21:19.592206', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS031', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-3AC16C6F', 'createdAt': '2026-09-11T03:23:26.975766', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS031', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-FCF06B65', 'createdAt': '2026-09-11T03:25:23.817860', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS031', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 48.2, 'Aggregate': 56.38, 'DevOps': 70.0}, 'Branch': 'CS', 'MailID': 'ramyankramya45@gmail.com', 'MobileNumber': '8088120586', 'RegNumber': 'DDAICS031', 'Scores': {'AI': {'API': 90, 'APIFoldersSQLNoSQL': 37, 'Amazon Backend': None, 'ChromaDB': 54, 'HuggingFace': 60, 'LLMTokenization': 0, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 60, 'Docker': 60, 'NGNIX': 90}}, 'StudentName': 'Ramya N K', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-336F5DE1', 'createdAt': '2026-09-11T03:21:19.592212', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS014', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-03E4A5E4', 'createdAt': '2026-09-11T03:23:26.975773', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS014', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-A8D54E8F', 'createdAt': '2026-09-11T03:25:23.817869', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS014', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 57.2, 'Aggregate': 56.25, 'DevOps': 54.67}, 'Branch': 'CS', 'MailID': 'impanaimpana97@gmail.com', 'MobileNumber': '8147106737', 'RegNumber': 'DDAICS014', 'Scores': {'AI': {'API': 90, 'APIFoldersSQLNoSQL': 27, 'Amazon Backend': None, 'ChromaDB': 47, 'HuggingFace': 60, 'LLMTokenization': 62, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 29, 'Docker': 70, 'NGNIX': 65}}, 'StudentName': 'Impana h b', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-16438D4B', 'createdAt': '2026-09-11T03:21:19.592243', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS028', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-1153ACAA', 'createdAt': '2026-09-11T03:23:26.975808', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS028', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-CF347D8A', 'createdAt': '2026-09-11T03:25:23.817918', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS028', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 53.0, 'Aggregate': 54.75, 'DevOps': 60.0}, 'Branch': 'CS', 'MailID': 'prerana.7650@gmail.com', 'MobileNumber': '7795077234', 'RegNumber': 'DDAICS028', 'Scores': {'AI': {'API': 90, 'APIFoldersSQLNoSQL': 30, 'Amazon Backend': None, 'ChromaDB': 39, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 60}}, 'StudentName': 'Prerana C N', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-523B7A38', 'createdAt': '2026-09-11T03:21:19.591832', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS042', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-51F350FF', 'createdAt': '2026-09-11T03:23:26.975379', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS042', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-B844803C', 'createdAt': '2026-09-11T03:25:23.817323', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS042', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 64.0, 'Aggregate': 68.67, 'DevOps': 78.0}, 'Branch': 'CS', 'MailID': 'vidyavidya27582@gmail.com', 'MobileNumber': '9380212829', 'RegNumber': 'DDAICS042', 'Scores': {'AI': {'API': 90, 'APIFoldersSQLNoSQL': 47, 'Amazon Backend': None, 'ChromaDB': 44, 'HuggingFace': 75, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 66, 'NGNIX': 90}}, 'StudentName': 'Vidya Vidya', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-215E66CB', 'createdAt': '2026-09-11T03:21:19.592352', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS015', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-35F52B70', 'createdAt': '2026-09-11T03:23:26.975916', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS015', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D433D8D5', 'createdAt': '2026-09-11T03:25:23.818067', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS015', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 48.0, 'Aggregate': 47.25, 'DevOps': 46.0}, 'Branch': 'CS', 'MailID': 'keerthanacv05@gmail.com', 'MobileNumber': '9731324218', 'RegNumber': 'DDAICS015', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 39, 'Amazon Backend': None, 'ChromaDB': 41, 'HuggingFace': 80, 'LLMTokenization': 0, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 0, 'Docker': 53, 'NGNIX': 85}}, 'StudentName': 'Keerthana C. V', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-2D446116', 'createdAt': '2026-09-11T03:21:19.592404', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS041', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-0208BA87', 'createdAt': '2026-09-11T03:23:26.975977', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS041', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-CD8DA141', 'createdAt': '2026-09-11T03:25:23.818149', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS041', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 59.0, 'Aggregate': 40.2, 'DevOps': 12.0}, 'Branch': 'CS', 'MailID': 'adithyapili732@gmail.com', 'MobileNumber': '7026156646', 'RegNumber': 'DDAICS041', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 41, 'Amazon Backend': None, 'ChromaDB': 56, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 24, 'Docker': 0}}, 'StudentName': 'Srujanasheela Adi', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-27825F5B', 'createdAt': '2026-09-11T03:21:19.592389', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS040', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-A06FF03E', 'createdAt': '2026-09-11T03:23:26.975959', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS040', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-EB949CE4', 'createdAt': '2026-09-11T03:25:23.818124', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS040', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 46.4, 'Aggregate': 42.12, 'DevOps': 35.0}, 'Branch': 'CS', 'MailID': 'srishtirameshk@gmail.com', 'MobileNumber': '8073215258', 'RegNumber': 'DDAICS040', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 60, 'Amazon Backend': None, 'ChromaDB': 0, 'HuggingFace': 85, 'LLMTokenization': 7, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 0, 'Docker': 10, 'NGNIX': 95}}, 'StudentName': 'Srishti K R', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-1B2593DC', 'createdAt': '2026-09-11T03:21:19.591534', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS003', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-70DD1209', 'createdAt': '2026-09-11T03:23:26.975039', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS003', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-CD51F8EF', 'createdAt': '2026-09-11T03:25:23.816873', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS003', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 79.4, 'Aggregate': 84.0, 'DevOps': 91.67}, 'Branch': 'CS', 'MailID': 'akhileshapjain2005@gmail.com', 'MobileNumber': '7899109103', 'RegNumber': 'DDAICS003', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 51, 'Amazon Backend': None, 'ChromaDB': 94, 'HuggingFace': 100, 'LLMTokenization': 72, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 75, 'Docker': 100, 'NGNIX': 100}}, 'StudentName': 'Akhilesh Jain', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-178D6F42', 'createdAt': '2026-09-11T03:21:19.591774', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS025', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-E214254F', 'createdAt': '2026-09-11T03:23:26.975312', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS025', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-69A26E30', 'createdAt': '2026-09-11T03:25:23.817234', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS025', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 75.25, 'Aggregate': 70.17, 'DevOps': 60.0}, 'Branch': 'CS', 'MailID': 'poornachandranandeppanavar7@gmail.com', 'MobileNumber': '8431613588', 'RegNumber': 'DDAICS025', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 69, 'Amazon Backend': None, 'ChromaDB': 87, 'LLMTokenization': 65, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 40, 'Docker': 80}}, 'StudentName': 'Poornachandra nandeppanavar', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-50CE199B', 'createdAt': '2026-09-11T03:21:19.591731', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS023', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-5F4F5843', 'createdAt': '2026-09-11T03:23:26.975263', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS023', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-C7F1470B', 'createdAt': '2026-09-11T03:25:23.817169', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS023', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 65.4, 'Aggregate': 71.12, 'DevOps': 80.67}, 'Branch': 'CS', 'MailID': 'poorvikabv117@gmail.com', 'MobileNumber': '9380784486', 'RegNumber': 'DDAICS023', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 50, 'Amazon Backend': None, 'ChromaDB': 67, 'HuggingFace': 65, 'LLMTokenization': 65, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 80, 'Docker': 77, 'NGNIX': 85}}, 'StudentName': 'Poorvika B V', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-AFF74AF5', 'createdAt': '2026-09-11T03:21:19.592227', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML042', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-88F5901B', 'createdAt': '2026-09-11T03:23:26.975790', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML042', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-EFAF11B5', 'createdAt': '2026-09-11T03:25:23.817894', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML042', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 47.5, 'Aggregate': 55.17, 'DevOps': 70.5}, 'Branch': 'AI/ML', 'MailID': 'vishwashiva1027@gmail.com', 'MobileNumber': '6366005435', 'RegNumber': 'DDAIAIML042', 'Scores': {'AI': {'API': 0, 'APIFoldersSQLNoSQL': 0, 'Amazon Backend': None, 'ChromaDB': 94, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 96}, 'DevOps': {'DevDockerGit': 51, 'GitDockerOperations': 90}}, 'StudentName': 'Vishwas (Vivek)', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-7B5ABB63', 'createdAt': '2026-09-11T03:21:19.592176', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS046', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-E8BE8BF9', 'createdAt': '2026-09-11T03:23:26.975736', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS046', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F60BFEE0', 'createdAt': '2026-09-11T03:25:23.817817', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS046', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 54.8, 'Aggregate': 57.62, 'DevOps': 62.33}, 'Branch': 'CS', 'MailID': 'yogithans41@gmail.com', 'MobileNumber': '7090319178', 'RegNumber': 'DDAICS046', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 43, 'Amazon Backend': None, 'ChromaDB': 39, 'HuggingFace': 65, 'LLMTokenization': 47, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 60, 'Docker': 67, 'NGNIX': 60}}, 'StudentName': 'Yogitha N S', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-8FD102C4', 'createdAt': '2026-09-11T03:21:19.591898', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS009', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-A1B7029F', 'createdAt': '2026-09-11T03:23:26.975441', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS009', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-6294C4F4', 'createdAt': '2026-09-11T03:25:23.817404', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS009', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 62.2, 'Aggregate': 65.75, 'DevOps': 71.67}, 'Branch': 'CS', 'MailID': 'chaturthipjain@gmail.com', 'MobileNumber': '9844837161', 'RegNumber': 'DDAICS009', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 43, 'Amazon Backend': None, 'ChromaDB': 56, 'HuggingFace': 70, 'LLMTokenization': 62, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 73, 'Docker': 87, 'NGNIX': 55}}, 'StudentName': 'Chaturthi p jain', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-38B45B3E', 'createdAt': '2026-09-11T03:21:19.591498', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML000', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-3606A42F', 'createdAt': '2026-09-11T03:23:26.975000', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML000', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-3F908F5F', 'createdAt': '2026-09-11T03:25:23.816819', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML000', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 84.75, 'Aggregate': 89.83, 'DevOps': 100.0}, 'Branch': 'AI/ML', 'MailID': 'jayanth55005@gmail.com', 'MobileNumber': '7975576855', 'RegNumber': 'DDAIAIML000', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 87, 'Amazon Backend': None, 'ChromaDB': 83, 'LLMTokenization': 89, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 100, 'Docker': 100}}, 'StudentName': 'Jayanth C D', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-99C506BC', 'createdAt': '2026-09-11T03:21:19.591545', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS019', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-75B0308C', 'createdAt': '2026-09-11T03:23:26.975051', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS019', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-7FEB063E', 'createdAt': '2026-09-11T03:25:23.816890', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS019', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 83.5, 'Aggregate': 83.5, 'DevOps': ''}, 'Branch': 'IS', 'MailID': 'kirangowdabk2134@gmail.com', 'MobileNumber': '6363338619', 'RegNumber': 'DDAIIS019', 'Scores': {'AI': {'API': 80, 'Amazon Backend': None, 'ChromaDB': 87, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'KIRAN KUMAR B N', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-ECBE294A', 'createdAt': '2026-09-11T03:21:19.591551', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS029', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-D039A166', 'createdAt': '2026-09-11T03:23:26.975057', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS029', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-4B6B7C65', 'createdAt': '2026-09-11T03:25:23.816898', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS029', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 81.0, 'Aggregate': 83.5, 'DevOps': 91.0}, 'Branch': 'CS', 'MailID': 'rajanishvgrvg@gmail.com', 'MobileNumber': '9449798596', 'RegNumber': 'DDAICS029', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 80, 'Amazon Backend': None, 'ChromaDB': 83, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 91}}, 'StudentName': 'RajanishVG RVG', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-8430E5AD', 'createdAt': '2026-09-11T03:21:19.591528', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS020', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-79C592D3', 'createdAt': '2026-09-11T03:23:26.975033', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS020', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-05E90DB0', 'createdAt': '2026-09-11T03:25:23.816864', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS020', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 84.5, 'Aggregate': 84.5, 'DevOps': ''}, 'Branch': 'CS', 'MailID': 'madankeshv18@gmail.com', 'MobileNumber': '7676141897', 'RegNumber': 'DDAICS020', 'Scores': {'AI': {'API': 80, 'Amazon Backend': None, 'ChromaDB': 89, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Madan keshav gowda', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-839A9B25', 'createdAt': '2026-09-11T03:21:19.592113', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS018', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-A68102C3', 'createdAt': '2026-09-11T03:23:26.975663', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS018', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-ED23D331', 'createdAt': '2026-09-11T03:25:23.817717', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS018', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 61.5, 'Aggregate': 60.14, 'DevOps': 58.33}, 'Branch': 'CS', 'MailID': 'kruthikagowda761@gmail.com', 'MobileNumber': '7022214587', 'RegNumber': 'DDAICS018', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 43, 'Amazon Backend': None, 'ChromaDB': 61, 'LLMTokenization': 62, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 47, 'Docker': 63, 'NGNIX': 65}}, 'StudentName': 'Kruthika A', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-ECE0E0D6', 'createdAt': '2026-09-11T03:21:19.591632', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS002', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-D07A4379', 'createdAt': '2026-09-11T03:23:26.975152', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS002', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-2C740A33', 'createdAt': '2026-09-11T03:25:23.817023', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS002', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 70.0, 'Aggregate': 74.62, 'DevOps': 82.33}, 'Branch': 'CS', 'MailID': 'aishwarya83626@gmail.com', 'MobileNumber': '6361927680', 'RegNumber': 'DDAICS002', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 64, 'Amazon Backend': None, 'ChromaDB': 59, 'HuggingFace': 90, 'LLMTokenization': 67, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 67, 'Docker': 80, 'NGNIX': 100}}, 'StudentName': 'Aishwarya HB', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-CC2AE80D', 'createdAt': '2026-09-11T03:21:19.591557', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS021', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-6C9C18C8', 'createdAt': '2026-09-11T03:23:26.975064', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS021', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-B50BBE03', 'createdAt': '2026-09-11T03:25:23.816906', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS021', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 79.33, 'Aggregate': 82.75, 'DevOps': 93.0}, 'Branch': 'CS', 'MailID': 'mahanteshp114@gmail.com', 'MobileNumber': '8660549241', 'RegNumber': 'DDAICS021', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 79, 'Amazon Backend': None, 'ChromaDB': 89, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 93}}, 'StudentName': 'Mahantesh Patil', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-054262C4', 'createdAt': '2026-09-11T03:21:19.591925', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS038', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-73D10DC7', 'createdAt': '2026-09-11T03:23:26.975471', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS038', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-6477D601', 'createdAt': '2026-09-11T03:25:23.817445', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS038', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 60.75, 'Aggregate': 65.17, 'DevOps': 74.0}, 'Branch': 'CS', 'MailID': 'sowbhagyahl2006@gmail.com', 'MobileNumber': '9108598179', 'RegNumber': 'DDAICS038', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 49, 'Amazon Backend': None, 'ChromaDB': 59, 'HuggingFace': 65, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 53, 'NGNIX': 95}}, 'StudentName': 'Sowbhagya H L', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-C52EEF62', 'createdAt': '2026-09-11T03:21:19.591789', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS035', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-EDBDA42F', 'createdAt': '2026-09-11T03:23:26.975330', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS035', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-8C2A70E2', 'createdAt': '2026-09-11T03:25:23.817258', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS035', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 63.2, 'Aggregate': 69.75, 'DevOps': 80.67}, 'Branch': 'CS', 'MailID': 'shrividyaskumar@gmail.com', 'MobileNumber': '9141569959', 'RegNumber': 'DDAICS035', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 63, 'Amazon Backend': None, 'ChromaDB': 61, 'HuggingFace': 60, 'LLMTokenization': 62, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 80, 'Docker': 77, 'NGNIX': 85}}, 'StudentName': 'Shrividya skumar', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-3455792E', 'createdAt': '2026-09-11T03:21:19.591616', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS006', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-0342616B', 'createdAt': '2026-09-11T03:23:26.975133', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS006', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-AABB7F0B', 'createdAt': '2026-09-11T03:25:23.816998', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS006', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 68.4, 'Aggregate': 75.25, 'DevOps': 86.67}, 'Branch': 'CS', 'MailID': 'archanaammu23014@gmail.com', 'MobileNumber': '8050867479', 'RegNumber': 'DDAICS006', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 55, 'Amazon Backend': None, 'ChromaDB': 67, 'HuggingFace': 85, 'LLMTokenization': 65, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 80, 'Docker': 80, 'NGNIX': 100}}, 'StudentName': 'Archana N L', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-168CCD76', 'createdAt': '2026-09-11T03:21:19.591736', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS012', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-B352E429', 'createdAt': '2026-09-11T03:23:26.975269', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS012', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-E65108CC', 'createdAt': '2026-09-11T03:25:23.817177', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS012', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 64.6, 'Aggregate': 70.88, 'DevOps': 81.33}, 'Branch': 'CS', 'MailID': 'hhithu285@gmail.com', 'MobileNumber': '9353110767', 'RegNumber': 'DDAICS012', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 57, 'Amazon Backend': None, 'ChromaDB': 67, 'HuggingFace': 70, 'LLMTokenization': 59, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 77, 'Docker': 67, 'NGNIX': 100}}, 'StudentName': 'Harshitha A M', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-6BF2D9D0', 'createdAt': '2026-09-11T03:21:19.592410', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS024', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-1ECEDC27', 'createdAt': '2026-09-11T03:23:26.975983', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS024', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-E57AED69', 'createdAt': '2026-09-11T03:25:23.818156', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS024', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 53.0, 'Aggregate': 39.75, 'DevOps': 0.0}, 'Branch': 'CS', 'MailID': 'pavanzari9565@gmail.com', 'MobileNumber': '9964073243', 'RegNumber': 'DDAICS024', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 0, 'Amazon Backend': None, 'ChromaDB': 89, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 0}}, 'StudentName': 'Pavan J', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-936CFC78', 'createdAt': '2026-09-11T03:21:19.592155', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS039', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-0209B21D', 'createdAt': '2026-09-11T03:23:26.975712', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS039', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-2612DDEC', 'createdAt': '2026-09-11T03:25:23.817784', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS039', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 61.5, 'Aggregate': 58.17, 'DevOps': 51.5}, 'Branch': 'CS', 'MailID': 'kmspoorthi9@gmail.com', 'MobileNumber': '8971696674', 'RegNumber': 'DDAICS039', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 63, 'Amazon Backend': None, 'ChromaDB': 83, 'LLMTokenization': 30, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 40, 'Docker': 63}}, 'StudentName': 'Spoorthi KM', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-A5BFD35B', 'createdAt': '2026-09-11T03:21:19.591649', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS017', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-5412D96F', 'createdAt': '2026-09-11T03:23:26.975171', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS017', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F54088B2', 'createdAt': '2026-09-11T03:25:23.817047', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS017', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 70.0, 'Aggregate': 73.5, 'DevOps': 79.33}, 'Branch': 'CS', 'MailID': 'krupamn321@gmail.com', 'MobileNumber': '8197379795', 'RegNumber': 'DDAICS017', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 63, 'Amazon Backend': None, 'ChromaDB': 83, 'HuggingFace': 70, 'LLMTokenization': 64, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 60, 'Docker': 83, 'NGNIX': 95}}, 'StudentName': 'Krupa M N', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-70B6A651', 'createdAt': '2026-09-11T03:21:19.591589', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS001', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-5C1000CC', 'createdAt': '2026-09-11T03:23:26.975102', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS001', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-7ADCD200', 'createdAt': '2026-09-11T03:25:23.816956', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS001', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 69.0, 'Aggregate': 76.38, 'DevOps': 88.67}, 'Branch': 'CS', 'MailID': 'adityapatil8660@gmail.com', 'MobileNumber': '8660961853', 'RegNumber': 'DDAICS001', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 65, 'Amazon Backend': None, 'ChromaDB': 82, 'HuggingFace': 70, 'LLMTokenization': 58, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 91, 'Docker': 80, 'NGNIX': 95}}, 'StudentName': 'Aditya Patil', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-3B38CD69', 'createdAt': '2026-09-11T03:21:19.592134', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS032', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-BB544E0A', 'createdAt': '2026-09-11T03:23:26.975687', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS032', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-3C5FF70C', 'createdAt': '2026-09-11T03:25:23.817751', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS032', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 53.0, 'Aggregate': 59.0, 'DevOps': 77.0}, 'Branch': 'CS', 'MailID': 'ruchithag28@gmail.com', 'MobileNumber': '9019590925', 'RegNumber': 'DDAICS032', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 0, 'Amazon Backend': None, 'ChromaDB': 89, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 77}}, 'StudentName': 'Ruchitha G', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-8C74B503', 'createdAt': '2026-09-11T03:21:19.591957', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS004', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-C59EF45A', 'createdAt': '2026-09-11T03:23:26.975507', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS004', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-86146294', 'createdAt': '2026-09-11T03:25:23.817493', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS004', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 77.5, 'Aggregate': 63.83, 'DevOps': 36.5}, 'Branch': 'CS', 'MailID': 'akshatatangadi05@gmail.com', 'MobileNumber': '7022117108', 'RegNumber': 'DDAICS004', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 62, 'Amazon Backend': None, 'ChromaDB': 78, 'HuggingFace': 100, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 73, 'NGNIX': 0}}, 'StudentName': 'Akshata Tangadi', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-58C7C5A2', 'createdAt': '2026-09-11T03:21:19.591978', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS034', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-EAB70EBF', 'createdAt': '2026-09-11T03:23:26.975525', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS034', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-2A3AF088', 'createdAt': '2026-09-11T03:25:23.817517', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS034', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 63.5, 'Aggregate': 63.5, 'DevOps': ''}, 'Branch': 'CS', 'MailID': 'sanjanasomubs@gmail.com', 'MobileNumber': '8317354456', 'RegNumber': 'DDAICS034', 'Scores': {'AI': {'API': 60, 'Amazon Backend': None, 'ChromaDB': 67, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Sanjana BS', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-9C6446FF', 'createdAt': '2026-09-11T03:21:19.591864', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS000', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-A41BF502', 'createdAt': '2026-09-11T03:23:26.975417', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS000', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D1FC03D0', 'createdAt': '2026-09-11T03:25:23.817372', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS000', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 67.0, 'Aggregate': 67.0, 'DevOps': ''}, 'Branch': 'CS', 'MailID': 'adarshay26@gmail.com', 'MobileNumber': '9591442134', 'RegNumber': 'DDAICS000', 'Scores': {'AI': {'API': 60, 'Amazon Backend': None, 'ChromaDB': 74, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Adarsh Bhaskara', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-509D158A', 'createdAt': '2026-09-11T03:21:19.591805', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS036', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-179E830C', 'createdAt': '2026-09-11T03:23:26.975349', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS036', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-0BFCA5D6', 'createdAt': '2026-09-11T03:25:23.817283', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS036', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 63.75, 'Aggregate': 69.5, 'DevOps': 81.0}, 'Branch': 'CS', 'MailID': 'aadi44738@gmail.com', 'MobileNumber': '9380974801', 'RegNumber': 'DDAICS036', 'Scores': {'AI': {'API': 60, 'Amazon Backend': None, 'ChromaDB': 67, 'HuggingFace': 65, 'LLMTokenization': 63, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'Docker': 87, 'NGNIX': 75}}, 'StudentName': 'Adithya Adi', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4FFC1125', 'createdAt': '2026-09-11T03:21:19.591622', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS033', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-89CAC879', 'createdAt': '2026-09-11T03:23:26.975140', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS033', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-9E5C3A31', 'createdAt': '2026-09-11T03:25:23.817006', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS033', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 73.2, 'Aggregate': 75.0, 'DevOps': 78.0}, 'Branch': 'CS', 'MailID': 'sachint337788@gmail.com', 'MobileNumber': '9663439358', 'RegNumber': 'DDAICS033', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 60, 'Amazon Backend': None, 'ChromaDB': 83, 'HuggingFace': 95, 'LLMTokenization': 68, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 67, 'Docker': 67, 'NGNIX': 100}}, 'StudentName': 'Sachin T', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-B0BE9627', 'createdAt': '2026-09-11T03:21:19.592066', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS010', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-6307A92B', 'createdAt': '2026-09-11T03:23:26.975620', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS010', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-588497F8', 'createdAt': '2026-09-11T03:25:23.817654', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS010', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 70.33, 'Aggregate': 61.5, 'DevOps': 35.0}, 'Branch': 'CS', 'MailID': 'jainhardik180@gmail.com', 'MobileNumber': '8310097281', 'RegNumber': 'DDAICS010', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 57, 'Amazon Backend': None, 'ChromaDB': 94, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 35}}, 'StudentName': 'Hardik jain ss', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-B14BE0D8', 'createdAt': '2026-09-11T03:21:19.591682', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS030', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-40B8FB91', 'createdAt': '2026-09-11T03:23:26.975208', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS030', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F8D35167', 'createdAt': '2026-09-11T03:25:23.817096', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS030', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 68.2, 'Aggregate': 73.0, 'DevOps': 81.0}, 'Branch': 'CS', 'MailID': 'rajyashreebd@gmail.com', 'MobileNumber': '8904171481', 'RegNumber': 'DDAICS030', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 57, 'Amazon Backend': None, 'ChromaDB': 61, 'HuggingFace': 100, 'LLMTokenization': 63, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 60, 'Docker': 83, 'NGNIX': 100}}, 'StudentName': 'rajyashree bd', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-10ED03CA', 'createdAt': '2026-09-11T03:21:19.591595', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS007', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-06F904A4', 'createdAt': '2026-09-11T03:23:26.975109', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS007', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-1C53BC32', 'createdAt': '2026-09-11T03:25:23.816964', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS007', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 74.8, 'Aggregate': 76.25, 'DevOps': 78.67}, 'Branch': 'CS', 'MailID': 'bhuvan.naik1008@gmail.com', 'MobileNumber': '9916633510', 'RegNumber': 'DDAICS007', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 49, 'Amazon Backend': None, 'ChromaDB': 72, 'HuggingFace': 100, 'LLMTokenization': 93, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 53, 'Docker': 83, 'NGNIX': 100}}, 'StudentName': 'Bhuvan Naik', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-85096A6D', 'createdAt': '2026-09-11T03:21:19.591962', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS019', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-35CE6348', 'createdAt': '2026-09-11T03:23:26.975513', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS019', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-28B7CF69', 'createdAt': '2026-09-11T03:25:23.817501', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS019', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 58.0, 'Aggregate': 63.83, 'DevOps': 75.5}, 'Branch': 'CS', 'MailID': 'lavanyagl972@gmail.com', 'MobileNumber': '9148181849', 'RegNumber': 'DDAICS019', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 40, 'Amazon Backend': None, 'ChromaDB': 72, 'HuggingFace': 70, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 51, 'NGNIX': 100}}, 'StudentName': 'Lavanya G L', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-67F47FEB', 'createdAt': '2026-09-11T03:21:19.591692', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS022', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-7B776433', 'createdAt': '2026-09-11T03:23:26.975220', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS022', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D3F81B83', 'createdAt': '2026-09-11T03:25:23.817112', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS022', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 62.5, 'Aggregate': 72.17, 'DevOps': 91.5}, 'Branch': 'CS', 'MailID': 'nisargamjain17@gmail.com', 'MobileNumber': '9164405003', 'RegNumber': 'DDAICS022', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 44, 'Amazon Backend': None, 'ChromaDB': 56, 'HuggingFace': 100, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 93, 'NGNIX': 90}}, 'StudentName': 'Nisarga M Jain', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-0A1485E9', 'createdAt': '2026-09-11T03:21:19.592248', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS037', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-730E6E1E', 'createdAt': '2026-09-11T03:23:26.975814', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS037', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-A942DA10', 'createdAt': '2026-09-11T03:25:23.817926', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS037', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 48.4, 'Aggregate': 54.62, 'DevOps': 65.0}, 'Branch': 'CS', 'MailID': 'harishr8274@gmail.com', 'MobileNumber': '8217468919', 'RegNumber': 'DDAICS037', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 51, 'Amazon Backend': None, 'ChromaDB': 39, 'HuggingFace': 50, 'LLMTokenization': 52, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 53, 'Docker': 67, 'NGNIX': 75}}, 'StudentName': 'Harish R', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-2E2D309C', 'createdAt': '2026-09-11T03:21:19.591638', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS044', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-8822DD3D', 'createdAt': '2026-09-11T03:23:26.975159', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS044', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-19E13011', 'createdAt': '2026-09-11T03:25:23.817031', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS044', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 72.0, 'Aggregate': 74.0, 'DevOps': 80.0}, 'Branch': 'CS', 'MailID': 'rathanrathan65blr@gmail.com', 'MobileNumber': '8951287915', 'RegNumber': 'DDAICS044', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 83, 'Amazon Backend': None, 'ChromaDB': 83, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 80}}, 'StudentName': 'Vivek hr', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4616CE49', 'createdAt': '2026-09-11T03:21:19.591821', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE012', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-F2EBDB76', 'createdAt': '2026-09-11T03:23:26.975367', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE012', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-2BD9CAB0', 'createdAt': '2026-09-11T03:25:23.817307', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE012', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 78.0, 'Aggregate': 68.75, 'DevOps': 41.0}, 'Branch': 'ECE', 'MailID': 'rameshdeepa493@gmail.com', 'MobileNumber': '7349209614', 'RegNumber': 'DDAIECE012', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 71, 'Amazon Backend': None, 'ChromaDB': 83, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 41}}, 'StudentName': 'Deepa Chougala', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-04BE9058', 'createdAt': '2026-09-11T03:21:19.592368', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS049', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-D30F4005', 'createdAt': '2026-09-11T03:23:26.975935', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS049', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D0E5487A', 'createdAt': '2026-09-11T03:25:23.818092', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS049', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 51.0, 'Aggregate': 44.5, 'DevOps': 31.5}, 'Branch': 'CS', 'MailID': 'incharajain2004@gmail.com', 'MobileNumber': '9482247873', 'RegNumber': 'DDAICS049', 'Scores': {'AI': {'API': 50, 'Amazon Backend': None, 'ChromaDB': 43, 'HuggingFace': 75, 'LLMTokenization': 36, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'Docker': 63, 'NGNIX': 0}}, 'StudentName': 'Inchara Jain MS', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-28A7B057', 'createdAt': '2026-09-11T03:21:19.592033', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS013', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-05CED847', 'createdAt': '2026-09-11T03:23:26.975584', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS013', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-408E8BF5', 'createdAt': '2026-09-11T03:25:23.817603', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS013', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 52.6, 'Aggregate': 62.5, 'DevOps': 79.0}, 'Branch': 'CS', 'MailID': 'harshitakc2005@gmail.com', 'MobileNumber': '7892841662', 'RegNumber': 'DDAICS013', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 44, 'Amazon Backend': None, 'ChromaDB': 67, 'HuggingFace': 75, 'LLMTokenization': 27, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 97, 'Docker': 70, 'NGNIX': 70}}, 'StudentName': 'Harshitha K c', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-C98A00D6', 'createdAt': '2026-09-11T03:21:19.592193', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS005', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-56A3099E', 'createdAt': '2026-09-11T03:23:26.975754', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS005', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-776FD623', 'createdAt': '2026-09-11T03:25:23.817843', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS005', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 57.0, 'Aggregate': 57.0, 'DevOps': ''}, 'Branch': 'CS', 'MailID': 'appujanoji401@gmail.com', 'MobileNumber': '9108416958', 'RegNumber': 'DDAICS005', 'Scores': {'AI': {'API': 40, 'Amazon Backend': None, 'ChromaDB': 46, 'HuggingFace': 85, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Apparay janoji', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-E19AE4B8', 'createdAt': '2026-09-11T03:21:19.591573', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS014', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-E54964F5', 'createdAt': '2026-09-11T03:23:26.975083', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS014', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-2EAB86CA', 'createdAt': '2026-09-11T03:25:23.816931', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS014', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 69.84, 'Aggregate': 79.9, 'DevOps': 96.67}, 'Branch': 'IS', 'MailID': 'hemalathaleela0@gmail.com', 'MobileNumber': '8660990945', 'RegNumber': 'DDAIIS014', 'Scores': {'AI': {'API': 40, 'APIFoldersSQLNoSQL': 71, 'Amazon Backend': None, 'ChromaDB': 72.22, 'HuggingFace': 70, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 96}, 'DevOps': {'DevDockerGit': 100, 'GitDockerOperations': 90, 'NGNIX': 100}}, 'StudentName': 'Hemalatha L', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-6F2F21AD', 'createdAt': '2026-09-11T03:21:19.591930', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS048', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-264FDC93', 'createdAt': '2026-09-11T03:23:26.975477', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS048', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-4B8505D4', 'createdAt': '2026-09-11T03:25:23.817453', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS048', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 33.0, 'Aggregate': 65.0, 'DevOps': 97.0}, 'Branch': 'IS', 'MailID': 'yashwanthkjyashwanthkj@gmail.com', 'MobileNumber': '7338015031', 'RegNumber': 'DDAIIS048', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 33, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 97}}, 'StudentName': 'Yashwanth k.j', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-82018BB3', 'createdAt': '2026-09-11T03:21:19.591946', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML037', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-5E3F22A1', 'createdAt': '2026-09-11T03:23:26.975495', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML037', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-DE0AF6BC', 'createdAt': '2026-09-11T03:25:23.817477', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML037', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 62.84, 'Aggregate': 64.58, 'DevOps': 66.75}, 'Branch': 'AI/ML', 'MailID': 'magadumsnehal72@gmail.com', 'MobileNumber': '7259750885', 'RegNumber': 'DDAIAIML037', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 42, 'Amazon Backend': None, 'ChromaDB': 72.22, 'HuggingFace': 90, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 60}, 'DevOps': {'DevDockerGit': 40, 'Docker': 77, 'GitDockerOperations': 50, 'NGNIX': 100}}, 'StudentName': 'Snehal Magadum', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-EE50DA56', 'createdAt': '2026-09-11T03:21:19.591843', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS004', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-750CA151', 'createdAt': '2026-09-11T03:23:26.975392', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS004', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-191749CB', 'createdAt': '2026-09-11T03:25:23.817340', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS004', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 58.21, 'Aggregate': 68.42, 'DevOps': 83.75}, 'Branch': 'IS', 'MailID': 'amruthak469@gmail.com', 'MobileNumber': '6366591407', 'RegNumber': 'DDAIIS004', 'Scores': {'AI': {'API': 0, 'APIFoldersSQLNoSQL': 29, 'Amazon Backend': None, 'ChromaDB': 81.25, 'HuggingFace': 80, 'LLMTokenization': 63, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 96}, 'DevOps': {'DevDockerGit': 93, 'Docker': 67, 'GitDockerOperations': 80, 'NGNIX': 95}}, 'StudentName': 'Amrutha S', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-E286CD8F', 'createdAt': '2026-09-11T03:21:19.591600', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS005', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-ED67CAC3', 'createdAt': '2026-09-11T03:23:26.975115', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS005', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D0AD3A44', 'createdAt': '2026-09-11T03:25:23.816972', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS005', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 75.75, 'Aggregate': 76.25, 'DevOps': 77.0}, 'Branch': 'IS', 'MailID': 'ankithalmjain@gmail.com', 'MobileNumber': '8296877558', 'RegNumber': 'DDAIIS005', 'Scores': {'AI': {'API': 40, 'APIFoldersSQLNoSQL': 60, 'Amazon Backend': None, 'ChromaDB': 87.5, 'HuggingFace': 95, 'LLMTokenization': 72, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 100}, 'DevOps': {'DevDockerGit': 93, 'Docker': 70, 'GitDockerOperations': 60, 'NGNIX': 85}}, 'StudentName': 'Ankitha L.M.jain', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-7E5BCC41', 'createdAt': '2026-09-11T03:21:19.591660', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML019', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-37CDA6CC', 'createdAt': '2026-09-11T03:23:26.975183', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML019', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-12DEF8B5', 'createdAt': '2026-09-11T03:25:23.817064', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML019', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 64.28, 'Aggregate': 73.41, 'DevOps': 91.67}, 'Branch': 'AI/ML', 'MailID': 'maazm5840@gmail.com', 'MobileNumber': '7795305690', 'RegNumber': 'DDAIAIML019', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 45, 'Amazon Backend': None, 'ChromaDB': 64.71, 'HuggingFace': 95, 'LLMTokenization': 81, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 40}, 'DevOps': {'DevDockerGit': 93, 'Docker': 97, 'NGNIX': 85}}, 'StudentName': 'Mohammed Maaz', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-FD7AB014', 'createdAt': '2026-09-11T03:21:19.591763', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS001', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-4B49815D', 'createdAt': '2026-09-11T03:23:26.975299', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS001', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-4C22EB5F', 'createdAt': '2026-09-11T03:25:23.817217', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS001', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 64.49, 'Aggregate': 70.29, 'DevOps': 79.0}, 'Branch': 'IS', 'MailID': 'aishwarya.vijaykumar.000@gmail.com', 'MobileNumber': '8296625894', 'RegNumber': 'DDAIIS001', 'Scores': {'AI': {'API': 90, 'APIFoldersSQLNoSQL': 57, 'Amazon Backend': None, 'ChromaDB': 52.94, 'HuggingFace': 100, 'LLMTokenization': 7, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 80}, 'DevOps': {'DevDockerGit': 91, 'Docker': 70, 'GitDockerOperations': 60, 'NGNIX': 95}}, 'StudentName': 'Aishwarya S V', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-DD26B6C4', 'createdAt': '2026-09-11T03:21:19.591562', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML040', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-0FD63CCE', 'createdAt': '2026-09-11T03:23:26.975070', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML040', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-6CF44E31', 'createdAt': '2026-09-11T03:25:23.816915', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML040', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 76.7, 'Aggregate': 80.92, 'DevOps': 87.25}, 'Branch': 'AI/ML', 'MailID': 'ushateju9108430@gmail.com', 'MobileNumber': '9108430114', 'RegNumber': 'DDAIAIML040', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 59, 'Amazon Backend': None, 'ChromaDB': 72.22, 'HuggingFace': 100, 'LLMTokenization': 67, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 92}, 'DevOps': {'DevDockerGit': 89, 'Docker': 90, 'GitDockerOperations': 70, 'NGNIX': 100}}, 'StudentName': 'Usha. N', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-9C3CE437', 'createdAt': '2026-09-11T03:21:19.591605', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS029', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-80DCB286', 'createdAt': '2026-09-11T03:23:26.975121', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS029', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-A2D32705', 'createdAt': '2026-09-11T03:25:23.816981', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS029', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 73.5, 'Aggregate': 76.25, 'DevOps': 79.0}, 'Branch': 'IS', 'MailID': 'shrigondpramod@gmail.com', 'MobileNumber': '9019291422', 'RegNumber': 'DDAIIS029', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 71, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 76}, 'DevOps': {'DevDockerGit': 88, 'GitDockerOperations': 70}}, 'StudentName': 'Pramod Shrigond', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-5419606C', 'createdAt': '2026-09-11T03:21:19.591768', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS010', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-AD41612A', 'createdAt': '2026-09-11T03:23:26.975306', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS010', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-C672AAAB', 'createdAt': '2026-09-11T03:25:23.817225', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS010', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 66.94, 'Aggregate': 70.21, 'DevOps': 75.67}, 'Branch': 'IS', 'MailID': 'ashwinikrashu1@gmail.com', 'MobileNumber': '8105531661', 'RegNumber': 'DDAIIS010', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 47, 'Amazon Backend': None, 'ChromaDB': 64.71, 'HuggingFace': 75, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 87, 'GitDockerOperations': 60, 'NGNIX': 80}}, 'StudentName': 'Ashwini R', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-67DF9AC6', 'createdAt': '2026-09-11T03:21:19.591951', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS027', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-2EBEDC50', 'createdAt': '2026-09-11T03:23:26.975501', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS027', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-026DAED0', 'createdAt': '2026-09-11T03:25:23.817485', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS027', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 55.93, 'Aggregate': 64.06, 'DevOps': 76.25}, 'Branch': 'IS', 'MailID': 'poornimahb85533@gmail.com', 'MobileNumber': '9972243634', 'RegNumber': 'DDAIIS027', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 42, 'Amazon Backend': None, 'ChromaDB': 70.59, 'HuggingFace': 55, 'LLMTokenization': 30, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 87, 'Docker': 63, 'GitDockerOperations': 80, 'NGNIX': 75}}, 'StudentName': 'Poornima Poornima', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-BB84F3F7', 'createdAt': '2026-09-11T03:21:19.591800', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS033', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-233ECF47', 'createdAt': '2026-09-11T03:23:26.975342', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS033', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-3173CB2C', 'createdAt': '2026-09-11T03:25:23.817274', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS033', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 67.33, 'Aggregate': 69.63, 'DevOps': 72.5}, 'Branch': 'IS', 'MailID': 'xyz497412@gmail.com', 'MobileNumber': '7899531031', 'RegNumber': 'DDAIIS033', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 50, 'Amazon Backend': None, 'ChromaDB': 66.67, 'HuggingFace': 75, 'LLMTokenization': 77, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 68}, 'DevOps': {'DevDockerGit': 87, 'Docker': 73, 'GitDockerOperations': 50, 'NGNIX': 80}}, 'StudentName': 'Rakshitha A', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-97D47771', 'createdAt': '2026-09-11T03:21:19.592161', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML029', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-04090A5D', 'createdAt': '2026-09-11T03:23:26.975718', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML029', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-5B64F076', 'createdAt': '2026-09-11T03:25:23.817793', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML029', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 49.0, 'Aggregate': 58.0, 'DevOps': 85.0}, 'Branch': 'AI/ML', 'MailID': 'sahanavm832@gmail.com', 'MobileNumber': '8792303614', 'RegNumber': 'DDAIAIML029', 'Scores': {'AI': {'API': 30, 'APIFoldersSQLNoSQL': 65, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 52}, 'DevOps': {'DevDockerGit': 85}}, 'StudentName': 'Sahana VM', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-7A876454', 'createdAt': '2026-09-11T03:21:19.592222', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS006', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-2F5002C3', 'createdAt': '2026-09-11T03:23:26.975785', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS006', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-EC4D46C3', 'createdAt': '2026-09-11T03:25:23.817885', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS006', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 42.0, 'Aggregate': 56.0, 'DevOps': 84.0}, 'Branch': 'IS', 'MailID': 'anupnaik1437@gmail.com', 'MobileNumber': '8197165922', 'RegNumber': 'DDAIIS006', 'Scores': {'AI': {'API': 40, 'APIFoldersSQLNoSQL': 44, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 84}}, 'StudentName': 'Anup Naik', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-6949ADEC', 'createdAt': '2026-09-11T03:21:19.591758', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS008', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-233FD7C7', 'createdAt': '2026-09-11T03:23:26.975293', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS008', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-3C29803D', 'createdAt': '2026-09-11T03:25:23.817209', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS008', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 68.68, 'Aggregate': 70.51, 'DevOps': 73.25}, 'Branch': 'IS', 'MailID': 'arundhathi2784@gmail.com', 'MobileNumber': '8660733360', 'RegNumber': 'DDAIIS008', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 57, 'Amazon Backend': None, 'ChromaDB': 47.06, 'HuggingFace': 90, 'LLMTokenization': 70, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 68}, 'DevOps': {'DevDockerGit': 80, 'Docker': 83, 'GitDockerOperations': 40, 'NGNIX': 90}}, 'StudentName': 'Arundhathi', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-B2063635', 'createdAt': '2026-09-11T03:21:19.591838', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS024', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-9C673718', 'createdAt': '2026-09-11T03:23:26.975386', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS024', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-9890BA5C', 'createdAt': '2026-09-11T03:25:23.817332', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS024', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 62.0, 'Aggregate': 68.67, 'DevOps': 77.0}, 'Branch': 'IS', 'MailID': 'manasa34844@gmail.com', 'MobileNumber': '9108697550', 'RegNumber': 'DDAIIS024', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 42, 'Amazon Backend': None, 'HuggingFace': 80, 'LLMTokenization': 48, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 80}, 'DevOps': {'DevDockerGit': 80, 'Docker': 73, 'GitDockerOperations': 60, 'NGNIX': 95}}, 'StudentName': 'Manasa Manasa', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-F5B1F7A1', 'createdAt': '2026-09-11T03:21:19.591643', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML012', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-CAF6FAEF', 'createdAt': '2026-09-11T03:23:26.975165', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML012', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-320782F0', 'createdAt': '2026-09-11T03:25:23.817039', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML012', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 70.04, 'Aggregate': 73.78, 'DevOps': 80.0}, 'Branch': 'AI/ML', 'MailID': 'harshichethups333@gmail.com', 'MobileNumber': '9019302845', 'RegNumber': 'DDAIAIML012', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 65, 'Amazon Backend': None, 'ChromaDB': 72.22, 'HuggingFace': 75, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 80, 'GitDockerOperations': 60, 'NGNIX': 100}}, 'StudentName': 'Harshitha P', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-7B40D706', 'createdAt': '2026-09-11T03:21:19.591584', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML039', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-EDF5A5AD', 'createdAt': '2026-09-11T03:23:26.975096', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML039', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-554153A7', 'createdAt': '2026-09-11T03:25:23.816948', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML039', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 71.33, 'Aggregate': 77.08, 'DevOps': 86.67}, 'Branch': 'AI/ML', 'MailID': 'tmtejaswini2004@gmail.com', 'MobileNumber': '6363781003', 'RegNumber': 'DDAIAIML039', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 65, 'Amazon Backend': None, 'ChromaDB': 66.67, 'HuggingFace': 95, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 80}, 'DevOps': {'DevDockerGit': 80, 'GitDockerOperations': 80, 'NGNIX': 100}}, 'StudentName': 'Tejaswini Tejaswini', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-56E65800', 'createdAt': '2026-09-11T03:21:19.591720', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS043', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-EA34D108', 'createdAt': '2026-09-11T03:23:26.975251', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS043', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-112F863B', 'createdAt': '2026-09-11T03:25:23.817153', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS043', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 64.2, 'Aggregate': 71.32, 'DevOps': 82.0}, 'Branch': 'IS', 'MailID': 'varshithah623@gmail.com', 'MobileNumber': '8431337498', 'RegNumber': 'DDAIIS043', 'Scores': {'AI': {'API': 30, 'APIFoldersSQLNoSQL': 63, 'Amazon Backend': None, 'ChromaDB': 72.22, 'HuggingFace': 80, 'LLMTokenization': 64, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 76}, 'DevOps': {'DevDockerGit': 80, 'Docker': 73, 'GitDockerOperations': 90, 'NGNIX': 85}}, 'StudentName': 'Varshitha M', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-A02E1A45', 'createdAt': '2026-09-11T03:21:19.591676', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML014', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-54CF0DBB', 'createdAt': '2026-09-11T03:23:26.975202', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML014', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-4EB6635C', 'createdAt': '2026-09-11T03:25:23.817088', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML014', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 68.24, 'Aggregate': 73.24, 'DevOps': 80.75}, 'Branch': 'AI/ML', 'MailID': 'kk3307004@gmail.com', 'MobileNumber': '7204616572', 'RegNumber': 'DDAIAIML014', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 44, 'Amazon Backend': None, 'ChromaDB': 94.44, 'HuggingFace': 75, 'LLMTokenization': 62, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 84}, 'DevOps': {'DevDockerGit': 80, 'Docker': 83, 'GitDockerOperations': 70, 'NGNIX': 90}}, 'StudentName': 'Keerthana G V', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4D5E2696', 'createdAt': '2026-09-11T03:21:19.592096', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML045', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-D4DB27F7', 'createdAt': '2026-09-11T03:23:26.975644', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML045', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-2A414BF7', 'createdAt': '2026-09-11T03:25:23.817689', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML045', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 56.53, 'Aggregate': 60.38, 'DevOps': 70.0}, 'Branch': 'AI/ML', 'MailID': 'rakshitamagadum30@gmail.com', 'MobileNumber': '8431724434', 'RegNumber': 'DDAIAIML045', 'Scores': {'AI': {'API': 40, 'APIFoldersSQLNoSQL': 52, 'Amazon Backend': None, 'ChromaDB': 66.67, 'LLMTokenization': 52, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 72}, 'DevOps': {'DevDockerGit': 80, 'Docker': 60}}, 'StudentName': 'Rakshita Magadum', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-5A3DE20A', 'createdAt': '2026-09-11T03:21:19.591627', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML033', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-52A30D99', 'createdAt': '2026-09-11T03:23:26.975146', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML033', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-8C21A95A', 'createdAt': '2026-09-11T03:25:23.817014', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML033', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 70.63, 'Aggregate': 74.78, 'DevOps': 81.0}, 'Branch': 'AI/ML', 'MailID': 'shreyasbm2k5@gmail.com', 'MobileNumber': '6361778392', 'RegNumber': 'DDAIAIML033', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 53, 'Amazon Backend': None, 'ChromaDB': 77.78, 'HuggingFace': 60, 'LLMTokenization': 61, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 92}, 'DevOps': {'DevDockerGit': 77, 'Docker': 87, 'GitDockerOperations': 70, 'NGNIX': 90}}, 'StudentName': 'Shreyas Bharadwaj BM', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-8F6A27A7', 'createdAt': '2026-09-11T03:21:19.591671', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS040', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-BB84176C', 'createdAt': '2026-09-11T03:23:26.975196', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS040', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-EF5722AC', 'createdAt': '2026-09-11T03:25:23.817080', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS040', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 75.72, 'Aggregate': 73.31, 'DevOps': 68.5}, 'Branch': 'IS', 'MailID': 'suhasharave1@gmail.com', 'MobileNumber': '9148665893', 'RegNumber': 'DDAIIS040', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 56, 'Amazon Backend': None, 'ChromaDB': 88.89, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 77, 'GitDockerOperations': 60}}, 'StudentName': 'Suhas Harave', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-CC360AB8', 'createdAt': '2026-09-11T03:21:19.591753', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS031', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-C8382BD4', 'createdAt': '2026-09-11T03:23:26.975287', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS031', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D791FCE3', 'createdAt': '2026-09-11T03:25:23.817201', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS031', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 59.67, 'Aggregate': 70.67, 'DevOps': 81.67}, 'Branch': 'IS', 'MailID': 'punyadapunyada9@gmail.com', 'MobileNumber': '6362681957', 'RegNumber': 'DDAIIS031', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 51, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 68}, 'DevOps': {'DevDockerGit': 75, 'GitDockerOperations': 70, 'NGNIX': 100}}, 'StudentName': 'Punya DA', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-D24DAF39', 'createdAt': '2026-09-11T03:21:19.591989', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS000', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-D53254E5', 'createdAt': '2026-09-11T03:23:26.975536', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS000', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-66F75870', 'createdAt': '2026-09-11T03:25:23.817533', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS000', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 61.49, 'Aggregate': 63.49, 'DevOps': 66.5}, 'Branch': 'IS', 'MailID': 'aishwaryaas2784@gmail.com', 'MobileNumber': '8123249218', 'RegNumber': 'DDAIIS000', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 47, 'Amazon Backend': None, 'ChromaDB': 52.94, 'HuggingFace': 70, 'LLMTokenization': 63, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 76}, 'DevOps': {'DevDockerGit': 73, 'Docker': 63, 'GitDockerOperations': 30, 'NGNIX': 100}}, 'StudentName': 'Aishwarya A S', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-33FAB20D', 'createdAt': '2026-09-11T03:21:19.591859', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML016', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-ACF3757A', 'createdAt': '2026-09-11T03:23:26.975410', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML016', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-401A6732', 'createdAt': '2026-09-11T03:25:23.817364', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML016', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 66.12, 'Aggregate': 67.17, 'DevOps': 68.75}, 'Branch': 'AI/ML', 'MailID': 'lakshmithag714@gmail.com', 'MobileNumber': '7676650440', 'RegNumber': 'DDAIAIML016', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 42, 'Amazon Backend': None, 'ChromaDB': 93.75, 'HuggingFace': 65, 'LLMTokenization': 42, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 84}, 'DevOps': {'DevDockerGit': 73, 'Docker': 87, 'GitDockerOperations': 30, 'NGNIX': 85}}, 'StudentName': 'Lakshmitha G', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-A05C4663', 'createdAt': '2026-09-11T03:21:19.592298', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML018', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-2A1B5BC3', 'createdAt': '2026-09-11T03:23:26.975868', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML018', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-251F1387', 'createdAt': '2026-09-11T03:25:23.818001', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML018', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 36.0, 'Aggregate': 50.0, 'DevOps': 73.33}, 'Branch': 'AI/ML', 'MailID': 'gowdas573201@gmail.com', 'MobileNumber': '6360571927', 'RegNumber': 'DDAIAIML018', 'Scores': {'AI': {'API': 0, 'APIFoldersSQLNoSQL': 54, 'Amazon Backend': None, 'HuggingFace': 75, 'LLMTokenization': 47, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 4}, 'DevOps': {'DevDockerGit': 73, 'Docker': 57, 'NGNIX': 90}}, 'StudentName': 'Mokshith S', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-1206287A', 'createdAt': '2026-09-11T03:21:19.591747', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML041', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-3810F5D5', 'createdAt': '2026-09-11T03:23:26.975281', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML041', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-4F557F20', 'createdAt': '2026-09-11T03:25:23.817193', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML041', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 59.06, 'Aggregate': 70.73, 'DevOps': 88.25}, 'Branch': 'AI/ML', 'MailID': 'varshinisn98@gmail.com', 'MobileNumber': '6361973832', 'RegNumber': 'DDAIAIML041', 'Scores': {'AI': {'API': 40, 'APIFoldersSQLNoSQL': 57, 'Amazon Backend': None, 'ChromaDB': 82.35, 'HuggingFace': 95, 'LLMTokenization': 0, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 80}, 'DevOps': {'DevDockerGit': 73, 'Docker': 90, 'GitDockerOperations': 90, 'NGNIX': 100}}, 'StudentName': 'VarshiniSN VarshiniSN', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-3C20E351', 'createdAt': '2026-09-11T03:21:19.592278', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML024', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-A8C0A839', 'createdAt': '2026-09-11T03:23:26.975844', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML024', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-99D89D44', 'createdAt': '2026-09-11T03:25:23.817967', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML024', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 37.33, 'Aggregate': 51.0, 'DevOps': 71.5}, 'Branch': 'AI/ML', 'MailID': 'pramodgowda968@gmail.com', 'MobileNumber': '6363154686', 'RegNumber': 'DDAIAIML024', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 32, 'Amazon Backend': None, 'LLMTokenization': 0, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 73, 'Docker': 70}}, 'StudentName': 'Pramod Gowda', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-D1F4346E', 'createdAt': '2026-09-11T03:21:19.591611', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS032', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-FFABCCDA', 'createdAt': '2026-09-11T03:23:26.975127', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS032', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-C3D9B9D0', 'createdAt': '2026-09-11T03:25:23.816990', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS032', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 75.42, 'Aggregate': 75.85, 'DevOps': 76.5}, 'Branch': 'IS', 'MailID': 'rakshithavenkatesh2805@gmail.com', 'MobileNumber': '7483345623', 'RegNumber': 'DDAIIS032', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 56, 'Amazon Backend': None, 'ChromaDB': 87.5, 'HuggingFace': 85, 'LLMTokenization': 56, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 73, 'Docker': 73, 'GitDockerOperations': 70, 'NGNIX': 90}}, 'StudentName': 'Rakshithavenkatesh28 V', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-61AE4687', 'createdAt': '2026-09-11T03:21:19.591579', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS046', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-9A58345E', 'createdAt': '2026-09-11T03:23:26.975089', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS046', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-028FE6CD', 'createdAt': '2026-09-11T03:25:23.816940', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS046', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 83.58, 'Aggregate': 79.86, 'DevOps': 73.67}, 'Branch': 'IS', 'MailID': 'yagneshms640@gmail.com', 'MobileNumber': '8073693464', 'RegNumber': 'DDAIIS046', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 66, 'Amazon Backend': None, 'ChromaDB': 88.89, 'HuggingFace': 95, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 71, 'GitDockerOperations': 60, 'NGNIX': 90}}, 'StudentName': 'LEGENDARY GAMERS', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-D9AAD258', 'createdAt': '2026-09-11T03:21:19.591665', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS037', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-2F22B94B', 'createdAt': '2026-09-11T03:23:26.975189', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS037', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F476EFA0', 'createdAt': '2026-09-11T03:25:23.817072', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS037', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 71.22, 'Aggregate': 73.39, 'DevOps': 77.0}, 'Branch': 'IS', 'MailID': 'sannidhirajesh10@gmail.com', 'MobileNumber': '7899313684', 'RegNumber': 'DDAIIS037', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 54, 'Amazon Backend': None, 'ChromaDB': 61.11, 'HuggingFace': 95, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 76}, 'DevOps': {'DevDockerGit': 71, 'GitDockerOperations': 60, 'NGNIX': 100}}, 'StudentName': 'Sannidhi S R', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-7E0EA6B7', 'createdAt': '2026-09-11T03:21:19.592077', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML007', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-0ECA3CA7', 'createdAt': '2026-09-11T03:23:26.975632', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML007', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-766924EF', 'createdAt': '2026-09-11T03:25:23.817672', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML007', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 48.33, 'Aggregate': 61.0, 'DevOps': 73.67}, 'Branch': 'AI/ML', 'MailID': 'dpkalmani062005@gmail.com', 'MobileNumber': '7483573314', 'RegNumber': 'DDAIAIML007', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 37, 'Amazon Backend': None, 'HuggingFace': 55, 'LLMTokenization': 53, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 69, 'Docker': 77, 'NGNIX': 75}}, 'StudentName': 'Daya K', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-9533E2CA', 'createdAt': '2026-09-11T03:21:19.591816', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS012', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-457D0ED6', 'createdAt': '2026-09-11T03:23:26.975361', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS012', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D0A82E27', 'createdAt': '2026-09-11T03:25:23.817299', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS012', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 67.84, 'Aggregate': 69.03, 'DevOps': 71.0}, 'Branch': 'IS', 'MailID': 'hamsasgowda328@gmail.com', 'MobileNumber': '6366562801', 'RegNumber': 'DDAIIS012', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 61, 'Amazon Backend': None, 'ChromaDB': 72.22, 'HuggingFace': 60, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 76}, 'DevOps': {'DevDockerGit': 68, 'GitDockerOperations': 60, 'NGNIX': 85}}, 'StudentName': 'Hamsa.S. Gowda', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-A5B8F424', 'createdAt': '2026-09-11T03:21:19.592259', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML021', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-36962864', 'createdAt': '2026-09-11T03:23:26.975826', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML021', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-171CE110', 'createdAt': '2026-09-11T03:25:23.817942', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML021', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 46.0, 'Aggregate': 53.0, 'DevOps': 67.0}, 'Branch': 'AI/ML', 'MailID': 'nithyagowda041123@gmail.com', 'MobileNumber': '7204779824', 'RegNumber': 'DDAIAIML021', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 42, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 67}}, 'StudentName': 'Nithya Gowda', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-39456760', 'createdAt': '2026-09-11T03:21:19.592233', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS017', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-FE9570C1', 'createdAt': '2026-09-11T03:23:26.975796', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS017', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-C1F4E6B7', 'createdAt': '2026-09-11T03:25:23.817902', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS017', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 52.2, 'Aggregate': 55.11, 'DevOps': 58.75}, 'Branch': 'IS', 'MailID': 'shreekkavana90@gmail.com', 'MobileNumber': '9902385589', 'RegNumber': 'DDAIIS017', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 50, 'Amazon Backend': None, 'HuggingFace': 55, 'LLMTokenization': 50, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 56}, 'DevOps': {'DevDockerGit': 67, 'Docker': 63, 'GitDockerOperations': 40, 'NGNIX': 65}}, 'StudentName': 'Kavana k', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-CFEDB632', 'createdAt': '2026-09-11T03:21:19.592150', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML023', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-8026DAB5', 'createdAt': '2026-09-11T03:23:26.975706', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML023', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-01560527', 'createdAt': '2026-09-11T03:25:23.817776', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML023', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 53.0, 'Aggregate': 58.22, 'DevOps': 64.75}, 'Branch': 'AI/ML', 'MailID': 'purushotthamasl@gmail.com', 'MobileNumber': '7022093026', 'RegNumber': 'DDAIAIML023', 'Scores': {'AI': {'API': 0, 'APIFoldersSQLNoSQL': 62, 'Amazon Backend': None, 'HuggingFace': 85, 'LLMTokenization': 58, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 60}, 'DevOps': {'DevDockerGit': 67, 'Docker': 67, 'GitDockerOperations': 30, 'NGNIX': 95}}, 'StudentName': 'PURUSHOTTHAMA SL', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-3F278D68', 'createdAt': '2026-09-11T03:21:19.591869', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML030', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-4E2E94FB', 'createdAt': '2026-09-11T03:23:26.975423', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML030', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-559E9E11', 'createdAt': '2026-09-11T03:25:23.817380', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML030', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 66.2, 'Aggregate': 66.42, 'DevOps': 66.75}, 'Branch': 'AI/ML', 'MailID': 'saniyakrpet@gmail.com', 'MobileNumber': '8088552860', 'RegNumber': 'DDAIAIML030', 'Scores': {'AI': {'API': 40, 'APIFoldersSQLNoSQL': 53, 'Amazon Backend': None, 'ChromaDB': 72.22, 'HuggingFace': 80, 'LLMTokenization': 64, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 67, 'Docker': 70, 'GitDockerOperations': 40, 'NGNIX': 90}}, 'StudentName': 'Saniya K J', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-E65F5454', 'createdAt': '2026-09-11T03:21:19.592000', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS035', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-DFB80BE6', 'createdAt': '2026-09-11T03:23:26.975548', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS035', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-1411E8D0', 'createdAt': '2026-09-11T03:25:23.817548', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS035', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 56.0, 'Aggregate': 63.0, 'DevOps': 70.0}, 'Branch': 'IS', 'MailID': 'shreyasnimbalkar01@gmail.com', 'MobileNumber': '7090967286', 'RegNumber': 'DDAIIS035', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 36, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 76}, 'DevOps': {'DevDockerGit': 60, 'GitDockerOperations': 80}}, 'StudentName': 'Shreyas Nimbalkar', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-F2B67805', 'createdAt': '2026-09-11T03:21:19.591994', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS042', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-968B7B71', 'createdAt': '2026-09-11T03:23:26.975543', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS042', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-DE9CF612', 'createdAt': '2026-09-11T03:25:23.817541', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS042', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 52.46, 'Aggregate': 63.48, 'DevOps': 80.0}, 'Branch': 'IS', 'MailID': 'thrushargowda@gmail.com', 'MobileNumber': '7411020175', 'RegNumber': 'DDAIIS042', 'Scores': {'AI': {'API': 10, 'APIFoldersSQLNoSQL': 37, 'Amazon Backend': None, 'ChromaDB': 77.78, 'HuggingFace': 90, 'LLMTokenization': 0, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 100}, 'DevOps': {'DevDockerGit': 60, 'Docker': 80, 'GitDockerOperations': 80, 'NGNIX': 100}}, 'StudentName': 'Thrusha Gowda', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-D41622C9', 'createdAt': '2026-09-11T03:21:19.592028', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML031', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-5363B690', 'createdAt': '2026-09-11T03:23:26.975578', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML031', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-42D053F0', 'createdAt': '2026-09-11T03:25:23.817594', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML031', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 64.28, 'Aggregate': 62.57, 'DevOps': 60.0}, 'Branch': 'AI/ML', 'MailID': 'seemahsseema38@gmail.com', 'MobileNumber': '8904065008', 'RegNumber': 'DDAIAIML031', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 53, 'Amazon Backend': None, 'ChromaDB': 66.67, 'HuggingFace': 80, 'LLMTokenization': 68, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 68}, 'DevOps': {'DevDockerGit': 60, 'Docker': 80, 'GitDockerOperations': 40, 'NGNIX': 60}}, 'StudentName': 'Seema . H . S Seema', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-222FCA75', 'createdAt': '2026-09-11T03:21:19.592022', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS041', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-BCB58F92', 'createdAt': '2026-09-11T03:23:26.975572', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS041', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-BD1A79F4', 'createdAt': '2026-09-11T03:25:23.817585', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS041', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 63.31, 'Aggregate': 62.61, 'DevOps': 61.67}, 'Branch': 'IS', 'MailID': 'suppisupritha651@gmail.com', 'MobileNumber': '6363463297', 'RegNumber': 'DDAIIS041', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 12, 'Amazon Backend': None, 'ChromaDB': 81.25, 'HuggingFace': 80, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 80}, 'DevOps': {'DevDockerGit': 60, 'GitDockerOperations': 50, 'NGNIX': 75}}, 'StudentName': 'Supritha Suppi', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-B91FCDFF', 'createdAt': '2026-09-11T03:21:19.592102', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML009', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-83E8A8B9', 'createdAt': '2026-09-11T03:23:26.975651', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML009', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-7984AC07', 'createdAt': '2026-09-11T03:25:23.817699', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML009', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 64.24, 'Aggregate': 60.24, 'DevOps': 54.25}, 'Branch': 'AI/ML', 'MailID': 'dhanyadarshini21@gmail.com', 'MobileNumber': '9663428931', 'RegNumber': 'DDAIAIML009', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 44, 'Amazon Backend': None, 'ChromaDB': 94.44, 'HuggingFace': 70, 'LLMTokenization': 55, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 72}, 'DevOps': {'DevDockerGit': 60, 'Docker': 77, 'GitDockerOperations': 0, 'NGNIX': 80}}, 'StudentName': 'dhanya darshini23', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4C972384', 'createdAt': '2026-09-11T03:21:19.591709', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML005', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-BE712AC1', 'createdAt': '2026-09-11T03:23:26.975239', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML005', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-8EB2970C', 'createdAt': '2026-09-11T03:25:23.817136', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML005', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 68.0, 'Aggregate': 71.71, 'DevOps': 76.67}, 'Branch': 'AI/ML', 'MailID': 'chiranthgowda2004hsn@gmail.com', 'MobileNumber': '9008803288', 'RegNumber': 'DDAIAIML005', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 48, 'Amazon Backend': None, 'HuggingFace': 90, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 64}, 'DevOps': {'DevDockerGit': 60, 'GitDockerOperations': 70, 'NGNIX': 100}}, 'StudentName': 'CHIRANTH GOWDA', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4D10774F', 'createdAt': '2026-09-11T03:21:19.592443', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML008', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-D97548C8', 'createdAt': '2026-09-11T03:23:26.976013', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML008', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-B2573A21', 'createdAt': '2026-09-11T03:25:23.818198', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML008', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 26.0, 'Aggregate': 31.8, 'DevOps': 55.0}, 'Branch': 'AI/ML', 'MailID': 'deepikadrdr255@gmail.com', 'MobileNumber': '7022406298', 'RegNumber': 'DDAIAIML008', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 54, 'Amazon Backend': None, 'ChromaDB': 0, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 0}, 'DevOps': {'DevDockerGit': 55}}, 'StudentName': 'Deepika R', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-45EF884D', 'createdAt': '2026-09-11T03:21:19.592171', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML013', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-513A3C3E', 'createdAt': '2026-09-11T03:23:26.975730', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML013', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-3622AE38', 'createdAt': '2026-09-11T03:25:23.817809', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML013', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 55.33, 'Aggregate': 57.7, 'DevOps': 61.25}, 'Branch': 'AI/ML', 'MailID': 'reethahemanthgowdaarmonisha@gmail.com', 'MobileNumber': '9731845242', 'RegNumber': 'DDAIAIML013', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 44, 'Amazon Backend': None, 'ChromaDB': 0, 'HuggingFace': 70, 'LLMTokenization': 58, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 80}, 'DevOps': {'DevDockerGit': 53, 'Docker': 67, 'GitDockerOperations': 40, 'NGNIX': 85}}, 'StudentName': 'Reetha Hemanth Gowda a r Monisha', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-5870F750', 'createdAt': '2026-09-11T03:21:19.592394', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS021', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-2DF7191C', 'createdAt': '2026-09-11T03:23:26.975965', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS021', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-16B3EC16', 'createdAt': '2026-09-11T03:25:23.818132', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS021', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 35.5, 'Aggregate': 41.33, 'DevOps': 53.0}, 'Branch': 'IS', 'MailID': 'atlikith33@gmail.com', 'MobileNumber': '8660761719', 'RegNumber': 'DDAIIS021', 'Scores': {'AI': {'API': 30, 'APIFoldersSQLNoSQL': 41, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 53}}, 'StudentName': 'Likhith Likhithrp', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-F3897D30', 'createdAt': '2026-09-11T03:21:19.591784', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML015', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-CE611397', 'createdAt': '2026-09-11T03:23:26.975324', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML015', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-6E6A6189', 'createdAt': '2026-09-11T03:25:23.817250', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML015', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 65.54, 'Aggregate': 69.92, 'DevOps': 76.5}, 'Branch': 'AI/ML', 'MailID': 'chinnig090@gmail.com', 'MobileNumber': '7349466579', 'RegNumber': 'DDAIAIML015', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 32, 'Amazon Backend': None, 'ChromaDB': 72.22, 'HuggingFace': 95, 'LLMTokenization': 56, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 53, 'Docker': 73, 'GitDockerOperations': 90, 'NGNIX': 90}}, 'StudentName': 'KrishneGowda KN', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-BEA8038C', 'createdAt': '2026-09-11T03:21:19.592128', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML002', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-07050FA5', 'createdAt': '2026-09-11T03:23:26.975682', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML002', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-319648A0', 'createdAt': '2026-09-11T03:25:23.817743', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML002', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 47.67, 'Aggregate': 59.7, 'DevOps': 77.75}, 'Branch': 'AI/ML', 'MailID': 'akshathatm2330@gmail.com', 'MobileNumber': '9353102276', 'RegNumber': 'DDAIAIML002', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 57, 'Amazon Backend': None, 'ChromaDB': 0, 'HuggingFace': 70, 'LLMTokenization': 53, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 56}, 'DevOps': {'DevDockerGit': 53, 'Docker': 83, 'GitDockerOperations': 80, 'NGNIX': 95}}, 'StudentName': 'Akshatha TM', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-29448B45', 'createdAt': '2026-09-11T03:21:19.591973', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS020', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-F1DA1D2A', 'createdAt': '2026-09-11T03:23:26.975519', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS020', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-3EFCF975', 'createdAt': '2026-09-11T03:25:23.817509', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS020', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 60.0, 'Aggregate': 63.67, 'DevOps': 68.25}, 'Branch': 'IS', 'MailID': 'kiranark065@gmail.com', 'MobileNumber': '6360162661', 'RegNumber': 'DDAIIS020', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 27, 'Amazon Backend': None, 'HuggingFace': 100, 'LLMTokenization': 51, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 72}, 'DevOps': {'DevDockerGit': 53, 'Docker': 70, 'GitDockerOperations': 50, 'NGNIX': 100}}, 'StudentName': 'Kirana RK', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-E2D9F6FA', 'createdAt': '2026-09-11T03:21:19.592140', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS018', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-DA9CB9F7', 'createdAt': '2026-09-11T03:23:26.975694', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS018', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-23FFACD9', 'createdAt': '2026-09-11T03:25:23.817759', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS018', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 63.09, 'Aggregate': 58.93, 'DevOps': 52.0}, 'Branch': 'IS', 'MailID': 'keerthana4053@gmail.com', 'MobileNumber': '8792524053', 'RegNumber': 'DDAIIS018', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 52, 'Amazon Backend': None, 'ChromaDB': 44.44, 'LLMTokenization': 81, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 53, 'Docker': 73, 'GitDockerOperations': 30}}, 'StudentName': 'Keerthana K', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-D8C126BB', 'createdAt': '2026-09-11T03:21:19.592123', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS036', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-46DCF9C6', 'createdAt': '2026-09-11T03:23:26.975675', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS036', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-FE4B04EB', 'createdAt': '2026-09-11T03:25:23.817735', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS036', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 54.0, 'Aggregate': 59.9, 'DevOps': 68.75}, 'Branch': 'IS', 'MailID': 'sahanack024@gmail.com', 'MobileNumber': '9535673915', 'RegNumber': 'DDAIIS036', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 31, 'Amazon Backend': None, 'ChromaDB': 0, 'HuggingFace': 90, 'LLMTokenization': 57, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 76}, 'DevOps': {'DevDockerGit': 53, 'Docker': 77, 'GitDockerOperations': 60, 'NGNIX': 85}}, 'StudentName': 'Sahana Ck', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-DA7FE455', 'createdAt': '2026-09-11T03:21:19.592293', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS023', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-A7A7A819', 'createdAt': '2026-09-11T03:23:26.975862', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS023', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-4EA68459', 'createdAt': '2026-09-11T03:25:23.817993', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS023', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 49.5, 'Aggregate': 50.17, 'DevOps': 51.5}, 'Branch': 'IS', 'MailID': 'mahalakshmimanya6191@gmail.com', 'MobileNumber': '7483378955', 'RegNumber': 'DDAIIS023', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 30, 'Amazon Backend': None, 'ChromaDB': 50, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 68}, 'DevOps': {'DevDockerGit': 53, 'GitDockerOperations': 50}}, 'StudentName': 'Mahalakshmi V', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-48F85CB8', 'createdAt': '2026-09-11T03:21:19.591941', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS007', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-475C5630', 'createdAt': '2026-09-11T03:23:26.975489', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS007', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-47D4C344', 'createdAt': '2026-09-11T03:25:23.817469', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS007', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 68.2, 'Aggregate': 64.67, 'DevOps': 60.25}, 'Branch': 'IS', 'MailID': 'arpithammarpitham45@gmail.com', 'MobileNumber': '7892258337', 'RegNumber': 'DDAIIS007', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 54, 'Amazon Backend': None, 'HuggingFace': 85, 'LLMTokenization': 50, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 72}, 'DevOps': {'DevDockerGit': 53, 'Docker': 73, 'GitDockerOperations': 50, 'NGNIX': 65}}, 'StudentName': 'Arpitha Arpitha', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-ABC08525', 'createdAt': '2026-09-11T03:21:19.591853', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML020', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-73C8C6AB', 'createdAt': '2026-09-11T03:23:26.975404', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML020', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-20A97E8A', 'createdAt': '2026-09-11T03:25:23.817356', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML020', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 67.13, 'Aggregate': 67.38, 'DevOps': 67.75}, 'Branch': 'AI/ML', 'MailID': 'moulyagowda956@gmail.com', 'MobileNumber': '6362774667', 'RegNumber': 'DDAIAIML020', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 49, 'Amazon Backend': None, 'ChromaDB': 77.78, 'HuggingFace': 80, 'LLMTokenization': 62, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 64}, 'DevOps': {'DevDockerGit': 51, 'Docker': 80, 'GitDockerOperations': 40, 'NGNIX': 100}}, 'StudentName': 'Moulya Gowda', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-6A197DA1', 'createdAt': '2026-09-11T03:21:19.592005', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS009', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-4D83AB16', 'createdAt': '2026-09-11T03:23:26.975555', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS009', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-C13589C7', 'createdAt': '2026-09-11T03:25:23.817558', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS009', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 64.33, 'Aggregate': 62.83, 'DevOps': 60.33}, 'Branch': 'IS', 'MailID': 'ashikagowda412@gmail.com', 'MobileNumber': '8861113078', 'RegNumber': 'DDAIIS009', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 40, 'Amazon Backend': None, 'ChromaDB': 66.67, 'HuggingFace': 85, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 80}, 'DevOps': {'DevDockerGit': 51, 'GitDockerOperations': 40, 'NGNIX': 90}}, 'StudentName': 'Ashika Gowda', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-6B1F8DE3', 'createdAt': '2026-09-11T03:21:19.592346', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS011', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-417A8C7C', 'createdAt': '2026-09-11T03:23:26.975910', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS011', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-3599672F', 'createdAt': '2026-09-11T03:25:23.818060', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS011', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 46.0, 'Aggregate': 47.5, 'DevOps': 49.0}, 'Branch': 'IS', 'MailID': 'ajaykumara24625@gmail.com', 'MobileNumber': '7892729954', 'RegNumber': 'DDAIIS011', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 46, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 49}}, 'StudentName': 'Ajay Kumar', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-CF9D47CB', 'createdAt': '2026-09-11T03:21:19.592056', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML010', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-332DFAB5', 'createdAt': '2026-09-11T03:23:26.975608', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML010', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-01951B87', 'createdAt': '2026-09-11T03:25:23.817637', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML010', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 66.81, 'Aggregate': 61.86, 'DevOps': 47.0}, 'Branch': 'AI/ML', 'MailID': 'mrdarsh1008@gmail.com', 'MobileNumber': '8746070108', 'RegNumber': 'DDAIAIML010', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 36, 'Amazon Backend': None, 'ChromaDB': 94.44, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 47}}, 'StudentName': 'Darsh Jain', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-C7256F04', 'createdAt': '2026-09-11T03:21:19.592183', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS038', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-0B52804E', 'createdAt': '2026-09-11T03:23:26.975742', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS038', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-542DF9F7', 'createdAt': '2026-09-11T03:25:23.817826', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS038', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 58.77, 'Aggregate': 57.61, 'DevOps': 55.67}, 'Branch': 'IS', 'MailID': 'shobhakiran2005@gmail.com', 'MobileNumber': '8618332791', 'RegNumber': 'DDAIIS038', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 41, 'Amazon Backend': None, 'ChromaDB': 58.85, 'HuggingFace': 60, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 64}, 'DevOps': {'DevDockerGit': 47, 'GitDockerOperations': 40, 'NGNIX': 80}}, 'StudentName': 'Shobha Shobha', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4693920D', 'createdAt': '2026-09-11T03:21:19.592107', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML003', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-51CF61EC', 'createdAt': '2026-09-11T03:23:26.975657', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML003', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-627027F4', 'createdAt': '2026-09-11T03:25:23.817708', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML003', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 65.95, 'Aggregate': 60.22, 'DevOps': 50.67}, 'Branch': 'AI/ML', 'MailID': 'anuanushaa2006@gmail.com', 'MobileNumber': '7349655206', 'RegNumber': 'DDAIAIML003', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 52, 'Amazon Backend': None, 'ChromaDB': 68.75, 'HuggingFace': 75, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 64}, 'DevOps': {'DevDockerGit': 47, 'GitDockerOperations': 30, 'NGNIX': 75}}, 'StudentName': 'Anusha A', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-219702D2', 'createdAt': '2026-09-11T03:21:19.592341', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS025', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-CCEF3C8B', 'createdAt': '2026-09-11T03:23:26.975904', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS025', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-C63493AC', 'createdAt': '2026-09-11T03:25:23.818052', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS025', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 43.0, 'Aggregate': 48.5, 'DevOps': 59.5}, 'Branch': 'IS', 'MailID': 'harshagowdaharsha82@gmail.com', 'MobileNumber': '9110606769', 'RegNumber': 'DDAIIS025', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 47, 'Amazon Backend': None, 'ChromaDB': 0, 'HuggingFace': 55, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 44, 'NGNIX': 75}}, 'StudentName': 'Harsha Harsha gowda', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-5CFD61D8', 'createdAt': '2026-09-11T03:21:19.592238', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS049', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-4F0D15FF', 'createdAt': '2026-09-11T03:23:26.975803', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS049', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F32C604F', 'createdAt': '2026-09-11T03:25:23.817910', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS049', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 43.75, 'Aggregate': 54.88, 'DevOps': 66.0}, 'Branch': 'IS', 'MailID': 'amulyagowda187@gmail.com', 'MobileNumber': '7022763136', 'RegNumber': 'DDAIIS049', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 41, 'Amazon Backend': None, 'HuggingFace': 0, 'LLMTokenization': 62, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 72}, 'DevOps': {'DevDockerGit': 44, 'Docker': 70, 'GitDockerOperations': 50, 'NGNIX': 100}}, 'StudentName': 'Amulyagowda2005 Amulyagowda2005', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-01FDA74F', 'createdAt': '2026-09-11T03:21:19.592373', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML027', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-F821178B', 'createdAt': '2026-09-11T03:23:26.975940', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML027', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-DD67E897', 'createdAt': '2026-09-11T03:25:23.818100', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML027', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 45.5, 'Aggregate': 44.33, 'DevOps': 42.0}, 'Branch': 'AI/ML', 'MailID': 'rohanshettyyadgir700@gmail.com', 'MobileNumber': '77954 35556', 'RegNumber': 'DDAIAIML027', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 31, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 42}}, 'StudentName': 'Rohan S', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-1DCAF1AA', 'createdAt': '2026-09-11T03:21:19.592166', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS026', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-7D70AA89', 'createdAt': '2026-09-11T03:23:26.975724', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS026', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-DCFCE9B3', 'createdAt': '2026-09-11T03:25:23.817801', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS026', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 71.0, 'Aggregate': 58.0, 'DevOps': 45.0}, 'Branch': 'IS', 'MailID': 'parshwanathbhasme92@gmail.com', 'MobileNumber': '9945989755', 'RegNumber': 'DDAIIS026', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 54, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 88}, 'DevOps': {'DevDockerGit': 40, 'GitDockerOperations': 50}}, 'StudentName': 'Parshwanath Bhasme', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-C7FB7C81', 'createdAt': '2026-09-11T03:21:19.592044', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML044', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-AFA21EE6', 'createdAt': '2026-09-11T03:23:26.975596', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML044', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-6206652F', 'createdAt': '2026-09-11T03:25:23.817619', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML044', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 73.0, 'Aggregate': 62.0, 'DevOps': 40.0}, 'Branch': 'AI/ML', 'MailID': 'yashanthshetty10@gmail.com', 'MobileNumber': '8431056208', 'RegNumber': 'DDAIAIML044', 'Scores': {'AI': {'API': 100, 'APIFoldersSQLNoSQL': 46, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 40}}, 'StudentName': 'Yashanth Shetty', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-E9CC5878', 'createdAt': '2026-09-11T03:21:19.592314', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML035', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-CEDDDBF7', 'createdAt': '2026-09-11T03:23:26.975886', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML035', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-9249E429', 'createdAt': '2026-09-11T03:25:23.818027', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML035', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 47.0, 'Aggregate': 49.7, 'DevOps': 53.75}, 'Branch': 'AI/ML', 'MailID': 'shwethakn978@gmail.com', 'MobileNumber': '6362293051', 'RegNumber': 'DDAIAIML035', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 47, 'Amazon Backend': None, 'ChromaDB': 0, 'HuggingFace': 60, 'LLMTokenization': 53, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 72}, 'DevOps': {'DevDockerGit': 40, 'Docker': 80, 'GitDockerOperations': 30, 'NGNIX': 65}}, 'StudentName': 'Shwetha Kn', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-59B089F7', 'createdAt': '2026-09-11T03:21:19.592362', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML025', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-9F3749C3', 'createdAt': '2026-09-11T03:23:26.975929', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML025', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-543A31DF', 'createdAt': '2026-09-11T03:25:23.818084', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML025', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 48.48, 'Aggregate': 46.49, 'DevOps': 42.5}, 'Branch': 'AI/ML', 'MailID': 'priyankapriyanka39611@gmail.com', 'MobileNumber': '9980406610', 'RegNumber': 'DDAIAIML025', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 43, 'Amazon Backend': None, 'ChromaDB': 52.94, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 48}, 'DevOps': {'DevDockerGit': 35, 'GitDockerOperations': 50}}, 'StudentName': 'Priyanka Priyanka', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-76F1E8B8', 'createdAt': '2026-09-11T03:21:19.592433', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS034', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-AF363200', 'createdAt': '2026-09-11T03:23:26.976001', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS034', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-000816AE', 'createdAt': '2026-09-11T03:25:23.818181', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS034', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 35.08, 'Aggregate': 34.25, 'DevOps': 33.0}, 'Branch': 'IS', 'MailID': 'suprithasupritha788@gmail.com', 'MobileNumber': '9353861540', 'RegNumber': 'DDAIIS034', 'Scores': {'AI': {'API': 30, 'Amazon Backend': None, 'ChromaDB': 31.25, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 44}, 'DevOps': {'DevDockerGit': 26, 'GitDockerOperations': 40}}, 'StudentName': 'Supritha S', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-794B56BD', 'createdAt': '2026-09-11T03:21:19.592288', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS013', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-DC2728A1', 'createdAt': '2026-09-11T03:23:26.975856', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS013', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F70CAB5B', 'createdAt': '2026-09-11T03:25:23.817985', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS013', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 57.0, 'Aggregate': 50.2, 'DevOps': 40.0}, 'Branch': 'IS', 'MailID': 'harshagk1604@gmail.com', 'MobileNumber': '7619167271', 'RegNumber': 'DDAIIS013', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 57, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 64}, 'DevOps': {'DevDockerGit': 0, 'GitDockerOperations': 80}}, 'StudentName': 'Harsha K', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-F8FB2D79', 'createdAt': '2026-09-11T03:21:19.591511', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML049', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-991FFB54', 'createdAt': '2026-09-11T03:23:26.975013', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML049', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-1FA554DC', 'createdAt': '2026-09-11T03:25:23.816836', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML049', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 82.83, 'Aggregate': 88.56, 'DevOps': 100.0}, 'Branch': 'AI/ML', 'MailID': 'pruthvirajsr.karthal115@gmail.com', 'MobileNumber': '8050011975', 'RegNumber': 'DDAIAIML049', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 32, 'Amazon Backend': None, 'ChromaDB': 100, 'HuggingFace': 90, 'LLMTokenization': 95, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 100}, 'DevOps': {'Docker': 100, 'GitDockerOperations': 100, 'NGNIX': 100}}, 'StudentName': 'pruthvi raj', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-8ED9F3B4', 'createdAt': '2026-09-11T03:21:19.591491', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML048', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-F002D96D', 'createdAt': '2026-09-11T03:23:26.974994', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML048', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-9789AA76', 'createdAt': '2026-09-11T03:25:23.816809', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML048', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 86.4, 'Aggregate': 90.29, 'DevOps': 100.0}, 'Branch': 'AI/ML', 'MailID': 'prajuprajwalgowda72@gmail.com', 'MobileNumber': '7483819348', 'RegNumber': 'DDAIAIML048', 'Scores': {'AI': {'API': 70, 'Amazon Backend': None, 'ChromaDB': 100, 'HuggingFace': 95, 'LLMTokenization': 99, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 68}, 'DevOps': {'Docker': 100, 'NGNIX': 100}}, 'StudentName': 'Prajwal PC', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-F25DA63E', 'createdAt': '2026-09-11T03:21:19.591481', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML032', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-6F4E2EFA', 'createdAt': '2026-09-11T03:23:26.974986', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML032', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F2123AF8', 'createdAt': '2026-09-11T03:25:23.816798', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML032', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 92.0, 'Aggregate': 92.75, 'DevOps': 94.0}, 'Branch': 'AI/ML', 'MailID': 'patilsammed188@gmail.com', 'MobileNumber': '9380861446', 'RegNumber': 'DDAIAIML032', 'Scores': {'AI': {'API': 80, 'Amazon Backend': None, 'ChromaDB': 100, 'HuggingFace': 85, 'LLMTokenization': 95, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 100}, 'DevOps': {'Docker': 97, 'GitDockerOperations': 90, 'NGNIX': 95}}, 'StudentName': 'Shantinath Patil', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-E2EF8ED7', 'createdAt': '2026-09-11T03:21:19.591540', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML036', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-D9C9583A', 'createdAt': '2026-09-11T03:23:26.975045', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML036', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-CB19DFBA', 'createdAt': '2026-09-11T03:25:23.816881', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML036', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 80.0, 'Aggregate': 83.75, 'DevOps': 90.0}, 'Branch': 'AI/ML', 'MailID': 'siddhanthsasture4@gmail.com', 'MobileNumber': '8310889326', 'RegNumber': 'DDAIAIML036', 'Scores': {'AI': {'API': 70, 'Amazon Backend': None, 'ChromaDB': 100, 'HuggingFace': 90, 'LLMTokenization': 88, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 52}, 'DevOps': {'Docker': 90, 'GitDockerOperations': 90, 'NGNIX': 90}}, 'StudentName': 'siddhanth sasture', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-97D9E93A', 'createdAt': '2026-09-11T03:21:19.591714', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML028', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-6DD45A72', 'createdAt': '2026-09-11T03:23:26.975245', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML028', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-912B3303', 'createdAt': '2026-09-11T03:25:23.817145', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML028', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 71.66, 'Aggregate': 71.66, 'DevOps': ''}, 'Branch': 'AI/ML', 'MailID': 'bukkarohit631@gmail.com', 'MobileNumber': '9113962099', 'RegNumber': 'DDAIAIML028', 'Scores': {'AI': {'API': 60, 'Amazon Backend': None, 'ChromaDB': 83.33, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Rohit', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-CAD7643B', 'createdAt': '2026-09-11T03:21:19.591725', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML050', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-8D561108', 'createdAt': '2026-09-11T03:23:26.975257', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML050', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-8E055996', 'createdAt': '2026-09-11T03:25:23.817161', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML050', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 65.65, 'Aggregate': 71.27, 'DevOps': 82.5}, 'Branch': 'AI/ML', 'MailID': 'sanchinzzsan@gmail.com', 'MobileNumber': '9945260560', 'RegNumber': 'DDAIAIML050', 'Scores': {'AI': {'API': 40, 'Amazon Backend': None, 'ChromaDB': 70.59, 'HuggingFace': 80, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 72}, 'DevOps': {'GitDockerOperations': 70, 'NGNIX': 95}}, 'StudentName': 'San Chinzzsan', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-E22A672F', 'createdAt': '2026-09-11T03:21:19.592071', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML011', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-F048B0E0', 'createdAt': '2026-09-11T03:23:26.975626', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML011', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-7FBDAC94', 'createdAt': '2026-09-11T03:25:23.817663', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML011', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 61.11, 'Aggregate': 61.11, 'DevOps': ''}, 'Branch': 'AI/ML', 'MailID': 'kempagowda468il.com@gmail.com', 'MobileNumber': '8149893955', 'RegNumber': 'DDAIAIML011', 'Scores': {'AI': {'Amazon Backend': None, 'ChromaDB': 61.11, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Gowda uadya kempa', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4E0E7105', 'createdAt': '2026-09-11T03:21:19.592011', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML022', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-8D599E90', 'createdAt': '2026-09-11T03:23:26.975560', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML022', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-2966387B', 'createdAt': '2026-09-11T03:25:23.817567', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML022', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 62.78, 'Aggregate': 62.78, 'DevOps': ''}, 'Branch': 'AI/ML', 'MailID': 'chidanithya@gmail.com', 'MobileNumber': '9916424945', 'RegNumber': 'DDAIAIML022', 'Scores': {'AI': {'API': 70, 'Amazon Backend': None, 'ChromaDB': 55.56, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Nithyananda C L', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-EE377C25', 'createdAt': '2026-09-11T03:21:19.592463', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML004', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-EB83D632', 'createdAt': '2026-09-11T03:23:26.976031', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML004', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-A8C343A3', 'createdAt': '2026-09-11T03:25:23.818222', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML004', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 25.0, 'Aggregate': 25.0, 'DevOps': ''}, 'Branch': 'AI/ML', 'MailID': 'nbahubali10@gmail.com', 'MobileNumber': '8746866606', 'RegNumber': 'DDAIAIML004', 'Scores': {'AI': {'API': 50, 'Amazon Backend': None, 'ChromaDB': 0, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Bahubali A N', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-A0079E99', 'createdAt': '2026-09-11T03:21:19.592449', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML017', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-33CE0B5E', 'createdAt': '2026-09-11T03:23:26.976019', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML017', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D20A3468', 'createdAt': '2026-09-11T03:25:23.818206', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML017', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 30.0, 'Aggregate': 30.0, 'DevOps': ''}, 'Branch': 'AI/ML', 'MailID': 'likhithlikhithrp5@gmail.com', 'MobileNumber': '9019026950', 'RegNumber': 'DDAIAIML017', 'Scores': {'AI': {'API': 60, 'Amazon Backend': None, 'ChromaDB': 0, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Likith RP', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-91A28AB8', 'createdAt': '2026-09-11T03:21:19.592017', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS047', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-09449B2B', 'createdAt': '2026-09-11T03:23:26.975567', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS047', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-BA4118FE', 'createdAt': '2026-09-11T03:25:23.817576', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS047', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 59.0, 'Aggregate': 62.67, 'DevOps': 70.0}, 'Branch': 'IS', 'MailID': 'marathiyashavant66@gmail.com', 'MobileNumber': '6361721511', 'RegNumber': 'DDAIIS047', 'Scores': {'AI': {'API': 50, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None, 'VectorDB': 68}, 'DevOps': {'GitDockerOperations': 70}}, 'StudentName': 'Yashavant Marathi', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-AD63FBFC', 'createdAt': '2026-09-11T03:21:19.591505', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE040', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-4D852B9E', 'createdAt': '2026-09-11T03:23:26.975007', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE040', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-9D0BBBF7', 'createdAt': '2026-09-11T03:25:23.816828', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE040', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 85.0, 'Aggregate': 88.75, 'DevOps': 100.0}, 'Branch': 'ECE', 'MailID': 'shivuba01@gmail.com', 'MobileNumber': '6364219405', 'RegNumber': 'DDAIECE040', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 87, 'Amazon Backend': None, 'ChromaDB': 98, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 100}}, 'StudentName': 'Shivu B,A', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-8782890B', 'createdAt': '2026-09-11T03:21:19.591984', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE045', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-70336D85', 'createdAt': '2026-09-11T03:23:26.975531', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE045', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-6BFC1554', 'createdAt': '2026-09-11T03:25:23.817525', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE045', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 51.33, 'Aggregate': 63.5, 'DevOps': 100.0}, 'Branch': 'ECE', 'MailID': 'vikasr.ecegrad@gmail.com', 'MobileNumber': '9353342249', 'RegNumber': 'DDAIECE045', 'Scores': {'AI': {'API': 0, 'APIFoldersSQLNoSQL': 82, 'Amazon Backend': None, 'ChromaDB': 72, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 100}}, 'StudentName': 'Vikas (R)', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-767FD263', 'createdAt': '2026-09-11T03:21:19.591742', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE049', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-365579F6', 'createdAt': '2026-09-11T03:23:26.975275', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE049', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-3C866DCF', 'createdAt': '2026-09-11T03:25:23.817185', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE049', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 61.0, 'Aggregate': 70.75, 'DevOps': 100.0}, 'Branch': 'ECE', 'MailID': 'yashavanthgowda494@gmail.com', 'MobileNumber': '8147426211', 'RegNumber': 'DDAIECE049', 'Scores': {'AI': {'API': 30, 'APIFoldersSQLNoSQL': 88, 'Amazon Backend': None, 'ChromaDB': 65, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 100}}, 'StudentName': 'Yashavanthgowda h b', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-09C586E3', 'createdAt': '2026-09-11T03:21:19.591517', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE041', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-CE4D4AAA', 'createdAt': '2026-09-11T03:23:26.975020', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE041', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F5A22701', 'createdAt': '2026-09-11T03:25:23.816845', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE041', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 85.0, 'Aggregate': 88.0, 'DevOps': 97.0}, 'Branch': 'ECE', 'MailID': 'siddeshsiddu6366348125@gmail.com', 'MobileNumber': '6360137422', 'RegNumber': 'DDAIECE041', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 88, 'Amazon Backend': None, 'ChromaDB': 87, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 97}}, 'StudentName': 'Siddesh B', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-6C068AC1', 'createdAt': '2026-09-11T03:21:19.591779', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE025', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-DFF46951', 'createdAt': '2026-09-11T03:23:26.975318', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE025', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-C6BCD0EB', 'createdAt': '2026-09-11T03:25:23.817242', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE025', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 47.0, 'Aggregate': 70.0, 'DevOps': 93.0}, 'Branch': 'ECE', 'MailID': 'likhithavasantha401@gmail.com', 'MobileNumber': '9019759321', 'RegNumber': 'DDAIECE025', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 47, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 93}}, 'StudentName': 'Likhitha vasantha', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-73D53528', 'createdAt': '2026-09-11T03:21:19.591848', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE005', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-673E5AA1', 'createdAt': '2026-09-11T03:23:26.975398', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE005', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F487536B', 'createdAt': '2026-09-11T03:25:23.817348', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE005', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 91.0, 'Aggregate': 68.25, 'DevOps': 0.0}, 'Branch': 'ECE', 'MailID': 'msm34006@gmail.com', 'MobileNumber': '9606139149', 'RegNumber': 'DDAIECE005', 'Scores': {'AI': {'API': 90, 'APIFoldersSQLNoSQL': 83, 'Amazon Backend': None, 'ChromaDB': 100, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 0}}, 'StudentName': 'Bhavana G', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-57D6415F', 'createdAt': '2026-09-11T03:21:19.591914', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE008', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-5B35EE9E', 'createdAt': '2026-09-11T03:23:26.975459', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE008', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-4697F7E7', 'createdAt': '2026-09-11T03:25:23.817429', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE008', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 87.33, 'Aggregate': 65.5, 'DevOps': 0.0}, 'Branch': 'ECE', 'MailID': 'cbm5652@gmail.com', 'MobileNumber': '8296907808', 'RegNumber': 'DDAIECE008', 'Scores': {'AI': {'API': 90, 'APIFoldersSQLNoSQL': 83, 'Amazon Backend': None, 'ChromaDB': 89, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 0}}, 'StudentName': 'Chandan M', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-0F7E07D6', 'createdAt': '2026-09-11T03:21:19.591903', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE039', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-167EDFAE', 'createdAt': '2026-09-11T03:23:26.975447', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE039', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-112AD017', 'createdAt': '2026-09-11T03:25:23.817412', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE039', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 57.0, 'Aggregate': 65.75, 'DevOps': 92.0}, 'Branch': 'ECE', 'MailID': 'sharathna2005@gmail.com', 'MobileNumber': '7338227015', 'RegNumber': 'DDAIECE039', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 38, 'Amazon Backend': None, 'ChromaDB': 83, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 92}}, 'StudentName': 'Sharath A', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-0F4BD456', 'createdAt': '2026-09-11T03:21:19.591826', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE037', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-30F21EA7', 'createdAt': '2026-09-11T03:23:26.975373', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE037', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-7E23C765', 'createdAt': '2026-09-11T03:25:23.817315', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE037', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 61.33, 'Aggregate': 68.75, 'DevOps': 91.0}, 'Branch': 'ECE', 'MailID': 'magadumamit058@gmail.com', 'MobileNumber': '6362967271', 'RegNumber': 'DDAIECE037', 'Scores': {'AI': {'API': 90, 'APIFoldersSQLNoSQL': 0, 'Amazon Backend': None, 'ChromaDB': 94, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 91}}, 'StudentName': 'sandhya sandhya', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-29A1DC3D', 'createdAt': '2026-09-11T03:21:19.591703', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE002', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-7B5B6122', 'createdAt': '2026-09-11T03:23:26.975232', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE002', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-91FBB4C5', 'createdAt': '2026-09-11T03:25:23.817128', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE002', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 65.33, 'Aggregate': 71.75, 'DevOps': 91.0}, 'Branch': 'ECE', 'MailID': 'koteakshata05@gmail.com', 'MobileNumber': '6362616181', 'RegNumber': 'DDAIECE002', 'Scores': {'AI': {'API': 20, 'APIFoldersSQLNoSQL': 83, 'Amazon Backend': None, 'ChromaDB': 93, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 91}}, 'StudentName': 'Akshata Kote', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-733C36A7', 'createdAt': '2026-09-11T03:21:19.592254', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE001', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-80191A91', 'createdAt': '2026-09-11T03:23:26.975820', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE001', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-4C11B275', 'createdAt': '2026-09-11T03:25:23.817934', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE001', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 42.33, 'Aggregate': 54.0, 'DevOps': 89.0}, 'Branch': 'ECE', 'MailID': 'aishwaryabd148@gmail.com', 'MobileNumber': '7892179745', 'RegNumber': 'DDAIECE001', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 57, 'Amazon Backend': None, 'ChromaDB': 0, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 89}}, 'StudentName': 'Aishwarya D', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-363E1FD7', 'createdAt': '2026-09-11T03:21:19.591908', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE024', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-91411DE6', 'createdAt': '2026-09-11T03:23:26.975453', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE024', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-54D47864', 'createdAt': '2026-09-11T03:25:23.817420', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE024', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 59.0, 'Aggregate': 65.75, 'DevOps': 86.0}, 'Branch': 'ECE', 'MailID': 'llikhithagowdamv@gmail.com', 'MobileNumber': '6362823127', 'RegNumber': 'DDAIECE024', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 59, 'Amazon Backend': None, 'ChromaDB': 68, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 86}}, 'StudentName': 'Likhitha mv', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-71275D62', 'createdAt': '2026-09-11T03:21:19.591919', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE000', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-45BC0675', 'createdAt': '2026-09-11T03:23:26.975465', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE000', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-1AA08300', 'createdAt': '2026-09-11T03:25:23.817437', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE000', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 47.0, 'Aggregate': 65.5, 'DevOps': 84.0}, 'Branch': 'AI/ML', 'MailID': 'abhijitshetti11@gmail.com', 'MobileNumber': '7411884494', 'RegNumber': 'DDAIECE000', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 47, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 84}}, 'StudentName': 'Abhijit Shetti', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-C9B7AAC5', 'createdAt': '2026-09-11T03:21:19.592378', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE036', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-D2E3FB7C', 'createdAt': '2026-09-11T03:23:26.975946', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE036', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-0F013EA6', 'createdAt': '2026-09-11T03:25:23.818108', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE036', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 45.67, 'Aggregate': 44.25, 'DevOps': 40.0}, 'Branch': 'ECE', 'MailID': 'uvsandesh72@gmail.com', 'MobileNumber': '9353484619', 'RegNumber': 'DDAIECE036', 'Scores': {'AI': {'API': 30, 'APIFoldersSQLNoSQL': 18, 'Amazon Backend': None, 'ChromaDB': 89, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 40}}, 'StudentName': 'sandesh uv', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-F1758EE5', 'createdAt': '2026-09-11T03:21:19.592145', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE006', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-E95584CF', 'createdAt': '2026-09-11T03:23:26.975700', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE006', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-A16D1FC4', 'createdAt': '2026-09-11T03:25:23.817768', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE006', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 37.0, 'Aggregate': 58.5, 'DevOps': 80.0}, 'Branch': 'ECE', 'MailID': 'bhuvans253@gmail.com', 'MobileNumber': '9108361471', 'RegNumber': 'DDAIECE006', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 37, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 80}}, 'StudentName': 's bhuvan', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-291E1481', 'createdAt': '2026-09-11T03:21:19.592038', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE032', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-FDFE4E08', 'createdAt': '2026-09-11T03:23:26.975590', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE032', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-1C89B007', 'createdAt': '2026-09-11T03:25:23.817611', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE032', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 56.67, 'Aggregate': 62.5, 'DevOps': 80.0}, 'Branch': 'ECE', 'MailID': 'spoorthysonu346@gmail.com', 'MobileNumber': '7760234936', 'RegNumber': 'DDAIECE032', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 56, 'Amazon Backend': None, 'ChromaDB': 44, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 80}}, 'StudentName': 'Spoorthy Sonu', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-53C2B1CF', 'createdAt': '2026-09-11T03:21:19.591698', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE027', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-ABE6E4F0', 'createdAt': '2026-09-11T03:23:26.975226', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE027', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-6102BFE2', 'createdAt': '2026-09-11T03:25:23.817120', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE027', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 69.33, 'Aggregate': 72.0, 'DevOps': 80.0}, 'Branch': 'ECE', 'MailID': 'navyashreen489@gmail.com', 'MobileNumber': '8197579584', 'RegNumber': 'DDAIECE027', 'Scores': {'AI': {'API': 80, 'APIFoldersSQLNoSQL': 71, 'Amazon Backend': None, 'ChromaDB': 57, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 80}}, 'StudentName': 'Navyashree Navya', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-3415BE1A', 'createdAt': '2026-09-11T03:21:19.592438', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE020', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-F9F0D581', 'createdAt': '2026-09-11T03:23:26.976007', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE020', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-EB0AEC40', 'createdAt': '2026-09-11T03:25:23.818189', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE020', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 16.0, 'Aggregate': 32.0, 'DevOps': 80.0}, 'Branch': 'ECE', 'MailID': 'jahnavihb1@gmail.com', 'MobileNumber': '8431378894', 'RegNumber': 'DDAIECE020', 'Scores': {'AI': {'API': 10, 'APIFoldersSQLNoSQL': 38, 'Amazon Backend': None, 'ChromaDB': 0, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 80}}, 'StudentName': 'Jahnavi B', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-AE3AAB38', 'createdAt': '2026-09-11T03:21:19.592264', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE030', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-DD990F49', 'createdAt': '2026-09-11T03:23:26.975832', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE030', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-FBBED48F', 'createdAt': '2026-09-11T03:25:23.817951', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE030', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 42.67, 'Aggregate': 52.0, 'DevOps': 80.0}, 'Branch': 'ECE', 'MailID': 'rakshithagk.14@gmail.com', 'MobileNumber': '6362228954', 'RegNumber': 'DDAIECE030', 'Scores': {'AI': {'API': 70, 'APIFoldersSQLNoSQL': 58, 'Amazon Backend': None, 'ChromaDB': 0, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 80}}, 'StudentName': 'Rakshitha K', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-941C2079', 'createdAt': '2026-09-11T03:21:19.592217', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE021', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-ECDDEA5D', 'createdAt': '2026-09-11T03:23:26.975779', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE021', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-5FD926A8', 'createdAt': '2026-09-11T03:25:23.817877', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE021', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 49.33, 'Aggregate': 56.25, 'DevOps': 77.0}, 'Branch': 'ECE', 'MailID': 'kanakaraju3262@gmail.com', 'MobileNumber': '7019196403', 'RegNumber': 'DDAIECE021', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 52, 'Amazon Backend': None, 'ChromaDB': 46, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 77}}, 'StudentName': 'Kanaka Raju', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-F7775523', 'createdAt': '2026-09-11T03:21:19.592420', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE034', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-53C4BE85', 'createdAt': '2026-09-11T03:23:26.975995', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE034', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-78F1DE76', 'createdAt': '2026-09-11T03:25:23.818172', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE034', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 33.67, 'Aggregate': 35.0, 'DevOps': 39.0}, 'Branch': 'ECE', 'MailID': 'sarapuresammed552@gmail.com', 'MobileNumber': '7624963142', 'RegNumber': 'DDAIECE034', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 41, 'Amazon Backend': None, 'ChromaDB': 0, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 39}}, 'StudentName': 'Sammed Halingali', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-5803F996', 'createdAt': '2026-09-11T03:21:19.592415', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE047', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-12B964E6', 'createdAt': '2026-09-11T03:23:26.975989', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE047', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-B3D7B7C3', 'createdAt': '2026-09-11T03:25:23.818165', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE047', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 35.0, 'Aggregate': 37.5, 'DevOps': 40.0}, 'Branch': 'ECE', 'MailID': 'vindyams993@gmail.com', 'MobileNumber': '7892821163', 'RegNumber': 'DDAIECE047', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 35, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 40}}, 'StudentName': 'Vindya ms', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-1BED4029', 'createdAt': '2026-09-11T03:21:19.591811', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE003', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-7BC13317', 'createdAt': '2026-09-11T03:23:26.975355', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE003', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-B9B366C0', 'createdAt': '2026-09-11T03:25:23.817291', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE003', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 68.67, 'Aggregate': 69.25, 'DevOps': 71.0}, 'Branch': 'ECE', 'MailID': 'apoorvaakiwate84@gmail.com', 'MobileNumber': '8088150586', 'RegNumber': 'DDAIECE003', 'Scores': {'AI': {'API': 40, 'APIFoldersSQLNoSQL': 83, 'Amazon Backend': None, 'ChromaDB': 83, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 71}}, 'StudentName': 'Apoorva Akiwate', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-09679835', 'createdAt': '2026-09-11T03:21:19.592118', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE022', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-3F31E063', 'createdAt': '2026-09-11T03:23:26.975669', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE022', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-DCD0C6D6', 'createdAt': '2026-09-11T03:25:23.817726', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE022', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 56.33, 'Aggregate': 60.0, 'DevOps': 71.0}, 'Branch': 'ECE', 'MailID': 'kavanank8792@gmail.com', 'MobileNumber': '8792441539', 'RegNumber': 'DDAIECE022', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 43, 'Amazon Backend': None, 'ChromaDB': 76, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 71}}, 'StudentName': 'Kavana k', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-6D297EC9', 'createdAt': '2026-09-11T03:21:19.591795', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE015', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-74CFBA6D', 'createdAt': '2026-09-11T03:23:26.975336', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE015', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-2B0B9C83', 'createdAt': '2026-09-11T03:25:23.817266', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE015', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 69.67, 'Aggregate': 69.75, 'DevOps': 70.0}, 'Branch': 'ECE', 'MailID': 'dhanyashrisv300@gmail.com', 'MobileNumber': '9380862693', 'RegNumber': 'DDAIECE015', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 70, 'Amazon Backend': None, 'ChromaDB': 89, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 70}}, 'StudentName': 'Dhanyashri SV', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-F2494080', 'createdAt': '2026-09-11T03:21:19.592335', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE007', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-82B735B6', 'createdAt': '2026-09-11T03:23:26.975898', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE007', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-BE30D78F', 'createdAt': '2026-09-11T03:25:23.818044', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE007', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 42.0, 'Aggregate': 48.75, 'DevOps': 69.0}, 'Branch': 'ECE', 'MailID': 'chaithrachaithramk988@gmail.com', 'MobileNumber': '9731404588', 'RegNumber': 'DDAIECE007', 'Scores': {'AI': {'API': 30, 'APIFoldersSQLNoSQL': 40, 'Amazon Backend': None, 'ChromaDB': 56, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 69}}, 'StudentName': 'Chaithra K', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-E734EA9C', 'createdAt': '2026-09-11T03:21:19.592061', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE019', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-79D8E8C7', 'createdAt': '2026-09-11T03:23:26.975614', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE019', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-AA2BC520', 'createdAt': '2026-09-11T03:25:23.817646', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE019', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 61.0, 'Aggregate': 61.75, 'DevOps': 64.0}, 'Branch': 'ECE', 'MailID': 'inavaloor@gmail.com', 'MobileNumber': '8618961537', 'RegNumber': 'DDAIECE019', 'Scores': {'AI': {'API': 50, 'APIFoldersSQLNoSQL': 55, 'Amazon Backend': None, 'ChromaDB': 78, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 64}}, 'StudentName': 'Iresh Navaloor', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4867B772', 'createdAt': '2026-09-11T03:21:19.591935', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE004', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-40D20BB5', 'createdAt': '2026-09-11T03:23:26.975483', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE004', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-5E48E582', 'createdAt': '2026-09-11T03:25:23.817461', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE004', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 66.0, 'Aggregate': 65.0, 'DevOps': 62.0}, 'Branch': 'ECE', 'MailID': 'ashwinid95355@gmail.com', 'MobileNumber': '9535520815', 'RegNumber': 'DDAIECE004', 'Scores': {'AI': {'API': 90, 'APIFoldersSQLNoSQL': 51, 'Amazon Backend': None, 'ChromaDB': 57, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 62}}, 'StudentName': 'Ashwini D Ashwini', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-6A3C973B', 'createdAt': '2026-09-11T03:21:19.592050', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE013', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-2EA3F517', 'createdAt': '2026-09-11T03:23:26.975602', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE013', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-0607F4D7', 'createdAt': '2026-09-11T03:25:23.817628', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE013', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 62.67, 'Aggregate': 62.0, 'DevOps': 60.0}, 'Branch': 'ECE', 'MailID': 'yy1837499@gmail.com', 'MobileNumber': '6362760106', 'RegNumber': 'DDAIECE013', 'Scores': {'AI': {'API': 60, 'APIFoldersSQLNoSQL': 45, 'Amazon Backend': None, 'ChromaDB': 83, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 60}}, 'StudentName': 'Deepak Yogesh', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-1C9B8823', 'createdAt': '2026-09-11T03:21:19.592357', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE028', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-05086A18', 'createdAt': '2026-09-11T03:23:26.975922', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE028', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-518A9AA5', 'createdAt': '2026-09-11T03:25:23.818076', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE028', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 58.67, 'Aggregate': 47.25, 'DevOps': 13.0}, 'Branch': 'ECE', 'MailID': 'sirishree7777@gmail.com', 'MobileNumber': '7483384664', 'RegNumber': 'DDAIECE028', 'Scores': {'AI': {'API': 30, 'APIFoldersSQLNoSQL': 52, 'Amazon Backend': None, 'ChromaDB': 94, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 13}}, 'StudentName': 'Siri Shree', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-17BB6B61', 'createdAt': '2026-09-11T03:21:19.592383', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE038', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-479B675F', 'createdAt': '2026-09-11T03:23:26.975953', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE038', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-73B95DAA', 'createdAt': '2026-09-11T03:25:23.818116', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE038', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 39.33, 'Aggregate': 43.75, 'DevOps': 57.0}, 'Branch': 'ECE', 'MailID': 'sanjanmr9@gmail.com', 'MobileNumber': '8660677969', 'RegNumber': 'DDAIECE038', 'Scores': {'AI': {'API': 30, 'APIFoldersSQLNoSQL': 44, 'Amazon Backend': None, 'ChromaDB': 44, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 57}}, 'StudentName': 'Sanjan R', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-9B1992C7', 'createdAt': '2026-09-11T03:21:19.592283', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE043', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-0A6AB4CC', 'createdAt': '2026-09-11T03:23:26.975850', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE043', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-BD9F9AA3', 'createdAt': '2026-09-11T03:25:23.817976', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE043', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 50.33, 'Aggregate': 51.0, 'DevOps': 53.0}, 'Branch': 'ECE', 'MailID': 'varshaaspreethu@gmail.com', 'MobileNumber': '7795076140', 'RegNumber': 'DDAIECE043', 'Scores': {'AI': {'API': 40, 'APIFoldersSQLNoSQL': 39, 'Amazon Backend': None, 'ChromaDB': 72, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 53}}, 'StudentName': 'Varsha shivram', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-C0E1F2D9', 'createdAt': '2026-09-11T03:21:19.592468', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE035', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-DFC41A37', 'createdAt': '2026-09-11T03:23:26.976037', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE035', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-DE5B5F47', 'createdAt': '2026-09-11T03:25:23.818231', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE035', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 38.0, 'Aggregate': 20.0, 'DevOps': 2.0}, 'Branch': 'ECE', 'MailID': 'sammedaski1425@gmail.com', 'MobileNumber': '8792661008', 'RegNumber': 'DDAIECE035', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 38, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 2}}, 'StudentName': 'Sammed Aski', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-3DAA8FE1', 'createdAt': '2026-09-11T03:21:19.592269', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE026', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-B0227492', 'createdAt': '2026-09-11T03:23:26.975838', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE026', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D868B17E', 'createdAt': '2026-09-11T03:25:23.817959', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE026', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 56.0, 'Aggregate': 51.5, 'DevOps': 47.0}, 'Branch': 'ECE', 'MailID': 'manikanthteli50@gmail.com', 'MobileNumber': '8147297410', 'RegNumber': 'DDAIECE026', 'Scores': {'AI': {'APIFoldersSQLNoSQL': 56, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 47}}, 'StudentName': 'Manikanth Teli', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-DF1D0117', 'createdAt': '2026-09-11T03:21:19.592399', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE010', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-91EA71C6', 'createdAt': '2026-09-11T03:23:26.975971', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE010', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-165C0A00', 'createdAt': '2026-09-11T03:25:23.818140', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE010', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 39.33, 'Aggregate': 41.0, 'DevOps': 46.0}, 'Branch': 'ECE', 'MailID': 'chethankumar.0628@gmail.com', 'MobileNumber': '8197231159', 'RegNumber': 'DDAIECE010', 'Scores': {'AI': {'API': 40, 'APIFoldersSQLNoSQL': 0, 'Amazon Backend': None, 'ChromaDB': 78, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'DevDockerGit': 46}}, 'StudentName': 'Chethan s', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-BA59C41A', 'createdAt': '2026-09-11T03:21:19.591568', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS039', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-F4456AE3', 'createdAt': '2026-09-11T03:23:26.975077', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS039', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-6BF75B41', 'createdAt': '2026-09-11T03:25:23.816923', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS039', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 80.0, 'Aggregate': 80.0, 'DevOps': ''}, 'Branch': 'IS', 'MailID': 'shreeramjagirdar@gmail.com', 'MobileNumber': '9880363250', 'RegNumber': 'DDAIIS039', 'Scores': {'AI': {'API': 80, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Shreeram', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-ADBB55DA', 'createdAt': '2026-09-11T03:21:19.592304', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML001', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-8D740770', 'createdAt': '2026-09-11T03:23:26.975874', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML001', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-8ED0EE9F', 'createdAt': '2026-09-11T03:25:23.818009', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML001', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 50.0, 'Aggregate': 50.0, 'DevOps': ''}, 'Branch': 'AI/ML', 'MailID': 'abhishekgabbur795@gmail.com', 'MobileNumber': '7406030111', 'RegNumber': 'DDAIAIML001', 'Scores': {'AI': {'API': 50, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Abhi', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-64F6FC4E', 'createdAt': '2026-09-11T03:21:19.592485', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS028', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-2D0AF4A0', 'createdAt': '2026-09-11T03:23:26.976056', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS028', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-77880C99', 'createdAt': '2026-09-11T03:25:23.818256', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS028', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 0.0, 'Aggregate': 0.0, 'DevOps': ''}, 'Branch': 'IS', 'MailID': 'preddychikkolli@gmail.com', 'MobileNumber': '9743548097', 'RegNumber': 'DDAIIS028', 'Scores': {'AI': {'API': 0, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Prakash', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-04FE5455', 'createdAt': '2026-09-11T03:21:19.592458', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML046', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-8EF01044', 'createdAt': '2026-09-11T03:23:26.976025', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML046', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F7194B66', 'createdAt': '2026-09-11T03:25:23.818214', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML046', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 30.0, 'Aggregate': 30.0, 'DevOps': ''}, 'Branch': 'AI/ML', 'MailID': 'charanp7557@gmail.com', 'MobileNumber': '9620894255', 'RegNumber': 'DDAIAIML046', 'Scores': {'AI': {'API': 30, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Charan', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-D75EE76C', 'createdAt': '2026-09-11T03:21:19.592479', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS022', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-D2B7CF26', 'createdAt': '2026-09-11T03:23:26.976049', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS022', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-D8EF7955', 'createdAt': '2026-09-11T03:25:23.818248', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIIS022', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 10.0, 'Aggregate': 10.0, 'DevOps': ''}, 'Branch': 'IS', 'MailID': 'maadhugowdatp@gmail.com', 'MobileNumber': '7483972384', 'RegNumber': 'DDAIIS022', 'Scores': {'AI': {'API': 10, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Mahadev prasad TP', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-724994AC', 'createdAt': '2026-09-11T03:21:19.591522', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE044', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-4CFB0DAC', 'createdAt': '2026-09-11T03:23:26.975026', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE044', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-CF73315B', 'createdAt': '2026-09-11T03:25:23.816854', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE044', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 85.0, 'Aggregate': 85.0, 'DevOps': ''}, 'Branch': 'ECE', 'MailID': 'veereshpujar474@gmail.com', 'MobileNumber': '8867244680', 'RegNumber': 'DDAIECE044', 'Scores': {'AI': {'API': 90, 'Amazon Backend': None, 'ChromaDB': 80, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Veeresh pujar', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-52A95DB4', 'createdAt': '2026-09-11T03:21:19.592199', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE011', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-9D3891A4', 'createdAt': '2026-09-11T03:23:26.975760', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE011', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-A748DDE9', 'createdAt': '2026-09-11T03:25:23.817851', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE011', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 57.0, 'Aggregate': 57.0, 'DevOps': ''}, 'Branch': 'ECE', 'MailID': 'darshangangatkar09@gmail.com', 'MobileNumber': '9019490388', 'RegNumber': 'DDAIECE011', 'Scores': {'AI': {'API': 70, 'Amazon Backend': None, 'ChromaDB': 44, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Darshan T B', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-03337B81', 'createdAt': '2026-09-11T03:21:19.591874', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE023', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-B198855B', 'createdAt': '2026-09-11T03:23:26.975429', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE023', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-EC75F451', 'createdAt': '2026-09-11T03:25:23.817388', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE023', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 66.0, 'Aggregate': 66.0, 'DevOps': ''}, 'Branch': 'ECE', 'MailID': 'keerthanamanjunath784@gmail.com', 'MobileNumber': '8310805845', 'RegNumber': 'DDAIECE023', 'Scores': {'AI': {'API': 60, 'Amazon Backend': None, 'ChromaDB': 72, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Keerthana MM', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-BED096D3', 'createdAt': '2026-09-11T03:21:19.591892', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE016', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-50E2F3F6', 'createdAt': '2026-09-11T03:23:26.975435', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE016', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-B9E551EC', 'createdAt': '2026-09-11T03:25:23.817396', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE016', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 66.0, 'Aggregate': 66.0, 'DevOps': ''}, 'Branch': 'ECE', 'MailID': 'bharathihampasagara@gmail.com', 'MobileNumber': '9740662800', 'RegNumber': 'DDAIECE016', 'Scores': {'AI': {'API': 60, 'Amazon Backend': None, 'ChromaDB': 72, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'H G Bharathi', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-D388A404', 'createdAt': '2026-09-11T03:21:19.591654', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE048', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-CA01FCE3', 'createdAt': '2026-09-11T03:23:26.975177', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE048', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-E7977F01', 'createdAt': '2026-09-11T03:25:23.817055', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE048', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 73.5, 'Aggregate': 73.5, 'DevOps': ''}, 'Branch': 'ECE', 'MailID': 'yadhukumar82@gmail.com', 'MobileNumber': '9686323757', 'RegNumber': 'DDAIECE048', 'Scores': {'AI': {'API': 60, 'Amazon Backend': None, 'ChromaDB': 87, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Yadukumar LP', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-C0466EF2', 'createdAt': '2026-09-11T03:21:19.592330', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE018', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-A79BAA90', 'createdAt': '2026-09-11T03:23:26.975892', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE018', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-B9C629A5', 'createdAt': '2026-09-11T03:25:23.818035', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE018', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 49.0, 'Aggregate': 49.0, 'DevOps': ''}, 'Branch': 'ECE', 'MailID': 'harshadhalingali1711@gmail.com', 'MobileNumber': '8217675813', 'RegNumber': 'DDAIECE018', 'Scores': {'AI': {'API': 20, 'Amazon Backend': None, 'ChromaDB': 78, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Harshad', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-86102DAF', 'createdAt': '2026-09-11T03:21:19.592309', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE033', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-EBCC4EE3', 'createdAt': '2026-09-11T03:23:26.975880', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE033', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-F510C6D0', 'createdAt': '2026-09-11T03:25:23.818018', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE033', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 50.0, 'Aggregate': 50.0, 'DevOps': ''}, 'Branch': 'ECE', 'MailID': 'lendes696@gmail.com', 'MobileNumber': '7090667930', 'RegNumber': 'DDAIECE033', 'Scores': {'AI': {'API': 50, 'Amazon Backend': None, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Sumit Lende', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-3291DD99', 'createdAt': '2026-09-11T03:21:19.592474', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE042', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-FF001753', 'createdAt': '2026-09-11T03:23:26.976043', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE042', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-01A4483B', 'createdAt': '2026-09-11T03:25:23.818239', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE042', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 20.0, 'Aggregate': 20.0, 'DevOps': ''}, 'Branch': 'ECE', 'MailID': 'tejukytejuky@gmail.com', 'MobileNumber': '8431929397', 'RegNumber': 'DDAIECE042', 'Scores': {'AI': {'API': 30, 'APIFoldersSQLNoSQL': 0, 'Amazon Backend': None, 'ChromaDB': 30, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'Teju K Y', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4D26ED97', 'createdAt': '2026-09-11T03:21:19.592091', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE031', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-14E22997', 'createdAt': '2026-09-11T03:23:26.975638', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE031', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-7F68F1D3', 'createdAt': '2026-09-11T03:25:23.817681', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIECE031', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 61.0, 'Aggregate': 61.0, 'DevOps': ''}, 'Branch': 'ECE', 'MailID': 'sammedhalingali1008jain@gmail.com', 'MobileNumber': '9353756839', 'RegNumber': 'DDAIECE031', 'Scores': {'AI': {'Amazon Backend': None, 'ChromaDB': 61, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {}}, 'StudentName': 'SAMMED MAHAVEER HALINGALI', 'TrackStatus': '', 'WatchListStatus': ''}, {'Assignment': [{'assignmentId': 'ASG-4D595631', 'createdAt': '2026-09-11T03:21:19.591447', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS048', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-E382E25D', 'createdAt': '2026-09-11T03:23:26.974967', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS048', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-5E04D429', 'createdAt': '2026-09-11T03:25:23.816773', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAICS048', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 100.0, 'Aggregate': 100.0, 'DevOps': 100.0}, 'Branch': 'CS', 'MailID': 'spoortikorigeri198@gmail.com', 'MobileNumber': '8123693894', 'RegNumber': 'DDAICS048', 'Scores': {'AI': {'Amazon Backend': None, 'HuggingFace': 100, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'NGNIX': 100}}, 'StudentName': 'Spoorti Korigeri', 'TrackStatus': '', 'WatchListStatus': 'Watchlist'}, {'Assignment': [{'assignmentId': 'ASG-4BFF7328', 'createdAt': '2026-09-11T03:21:19.592188', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML047', 'topic': 'Amazon Backend', 'track': 'AI'}, {'assignmentId': 'ASG-AA46089B', 'createdAt': '2026-09-11T03:23:26.975748', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML047', 'topic': 'Tokeniser Algorithm', 'track': 'AI'}, {'assignmentId': 'ASG-5E195796', 'createdAt': '2026-09-11T03:25:23.817835', 'score': None, 'status': 'PENDING', 'studentRegisterNumber': 'DDAIAIML047', 'topic': 'RAG', 'track': 'AI'}], 'AssignmentTopics': {'AI': ['Amazon Backend', 'Tokeniser Algorithm', 'RAG']}, 'Attandance': {'Day1': '', 'Day2': ''}, 'AverageScore': {'AI': 45.0, 'Aggregate': 57.5, 'DevOps': 70.0}, 'Branch': 'AI/ML', 'MailID': 'nithyagowda041123@gmail.com', 'MobileNumber': '7204779824', 'RegNumber': 'DDAIAIML047', 'Scores': {'AI': {'Amazon Backend': None, 'HuggingFace': 45, 'RAG': None, 'Tokeniser Algorithm': None}, 'DevOps': {'NGNIX': 70}}, 'StudentName': 'Nithya Gowda', 'TrackStatus': '', 'WatchListStatus': ''}]
_build_student_roster(test_data)
'''