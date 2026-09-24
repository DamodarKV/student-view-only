"""Admin overview API — powers the 'Admin' tab of the dashboard."""
from fastapi import APIRouter, HTTPException, Query

import data

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/tracks")
def list_tracks():
    return {"options": data.TRACK_FILTER_OPTIONS}


@router.get("/status")
def get_status():
    return {
        "dataSource": data.DATA_SOURCE,
        "studentCount": len(data.STUDENT_ROSTER),
        "trackCount": len(data.TRACK_FILTER_OPTIONS) - 1,
    }


@router.post("/reload")
def reload_data():
    data.reload_students()
    return {
        "status": "success",
        "dataSource": data.DATA_SOURCE,
        "studentCount": len(data.STUDENT_ROSTER),
    }


@router.get("/overview")
def get_overview(track: str = Query("All tracks")):
    """Everything the top of the admin page needs in one call: stat cards,
    the assessment-by-track chart, and the warning donut."""
    print(f"[admin] rendering Admin page (track={track!r})")
    if track not in data.TRACK_FILTER_OPTIONS:
        raise HTTPException(status_code=404, detail="Unknown track")
    d = data.get_track_data(track)
    assessment = [item for item in d["assessment"] if item.get("track") != "DevDockerGit"]
    return {
        "track": track,
        "stats": data.build_stats(d),
        "assessment": assessment,
        "warning": d["warning"],
        "panelCounts": d.get("panelCounts", {"selected": 0, "rejected": 0}),
    }


@router.get("/students")
def get_students(track: str = Query("All tracks"), category: str = Query("selected")):
    norm_cat = category.strip().lower()
    if norm_cat not in data.PANEL_CATEGORY_META and category not in data.CATEGORY_META:
        raise HTTPException(status_code=404, detail="Unknown category")
    d = data.get_track_data(track)
    if norm_cat in data.PANEL_CATEGORY_META:
        meta = data.PANEL_CATEGORY_META[norm_cat]
        rows = d.get("panelStudents", {}).get(norm_cat) or d.get("students", {}).get(norm_cat, [])
        counts = d.get("panelCounts", {"selected": 0, "rejected": 0})
        return {
            "category": norm_cat,
            "meta": meta,
            "counts": counts,
            "students": rows,
        }
    rows = d["students"].get(category, [])
    return {
        "category": category,
        "meta": data.CATEGORY_META[category],
        "counts": d.get("warning", {}),
        "students": rows,
    }


@router.get("/attendance")
def get_attendance(track: str = Query("All tracks")):
    d = data.get_track_data(track)
    attendance = d["attendance"]
    chart_data = [
        {
            "label": data.format_session_date(s["date"]),
            "present": s["present"],
            "absent": s["total"] - s["present"],
            "total": s["total"],
            "pct": round((s["present"] / s["total"]) * 100),
        }
        for s in attendance
    ]
    avg_present = round(sum(s["present"] for s in attendance) / len(attendance))
    avg_pct = round(
        (sum(s["present"] for s in attendance) / sum(s["total"] for s in attendance)) * 100
    )
    table = [
        {
            "date": data.format_session_date(s["date"]),
            "day": s["day"],
            "present": s["present"],
            "total": s["total"],
            "pct": round((s["present"] / s["total"]) * 100),
        }
        for s in attendance
    ]
    return {
        "track": track,
        "sessionCount": len(attendance),
        "avgPresent": avg_present,
        "avgPct": avg_pct,
        "chartData": chart_data,
        "table": table,
    }


@router.get("/scores")
def get_scores(track: str = Query("All tracks")):
    d = data.get_track_data(track)
    daily_scores = d["dailyScores"]
    chart_data = [{"label": data.format_session_date(s["date"]), "avgScore": s["avgScore"]} for s in daily_scores]
    overall_avg = round(sum(s["avgScore"] for s in daily_scores) / len(daily_scores))
    trend = daily_scores[-1]["avgScore"] - daily_scores[0]["avgScore"] if len(daily_scores) > 1 else 0
    table = [
        {"date": data.format_session_date(s["date"]), "day": s["day"], "avgScore": s["avgScore"]}
        for s in daily_scores
    ]
    return {
        "track": track,
        "sessionCount": len(daily_scores),
        "overallAvg": overall_avg,
        "trend": trend,
        "chartData": chart_data,
        "table": table,
    }


@router.get("/watchlist")
def get_watchlist(track: str = Query("All tracks")):
    d = data.get_track_data(track)
    return {"track": track, "students": d["watchlistStudents"]}


@router.get("/risk")
def get_at_risk(track: str = Query("All tracks")):
    students = data.get_at_risk_students(track)
    return {"track": track, "count": len(students), "students": students}


@router.get("/student/{register_number}")
def get_admin_student_detail(register_number: str):
    print(f"[admin] rendering Admin Student Detail page (register_number={register_number!r})")
    student = data.find_admin_student(register_number)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    roster_shape = data.admin_student_to_roster_shape(student)
    detail = data.build_roster_student_detail(roster_shape)
    return {"student": roster_shape, "detail": detail}


from pydantic import BaseModel
from typing import Optional

class AdminWatchlistTogglePayload(BaseModel):
    registerNumber: str
    watchlist: Optional[bool] = None

@router.post("/watchlist/toggle")
def toggle_admin_watchlist(payload: AdminWatchlistTogglePayload):
    student_obj = data.find_roster_student(payload.registerNumber)
    if not student_obj:
        raise HTTPException(status_code=404, detail="Student not found")
    new_state = (not student_obj.get("onWatchlist", False)) if payload.watchlist is None else payload.watchlist
    data.set_student_watchlist(payload.registerNumber, new_state)
    return {
        "status": "success",
        "registerNumber": payload.registerNumber,
        "onWatchlist": new_state,
        "watchlistCount": len(data.get_watchlist_students()),
        "message": f"Student {student_obj['name']} {'added to' if new_state else 'removed from'} watchlist",
    }


class AdminStudentStatusPayload(BaseModel):
    registerNumber: str
    status: str


@router.post("/student/status")
@router.put("/student/status")
def update_admin_student_status(payload: AdminStudentStatusPayload):
    student_obj = data.find_roster_student(payload.registerNumber)
    if not student_obj:
        raise HTTPException(status_code=404, detail="Student not found")
    norm_status = "Selected" if payload.status.strip().lower() in ("selected", "select") else "Rejected"
    data.set_student_status(payload.registerNumber, norm_status)
    all_data = data.get_track_data("All tracks")
    return {
        "status": "success",
        "registerNumber": payload.registerNumber,
        "studentStatus": norm_status,
        "counts": all_data.get("panelCounts", {"selected": 0, "rejected": 0}),
        "message": f"Student {student_obj['name']} status updated to {norm_status}",
    }
