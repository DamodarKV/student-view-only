from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import data

router = APIRouter(prefix="/api/student", tags=["student"])


class UpdateStudentScoresPayload(BaseModel):
    registerNumber: str = Field(..., alias="RegNumber")
    aiScore: Optional[float] = Field(None, alias="AIScore")
    devopsScore: Optional[float] = Field(None, alias="DevOpsScore")

    class Config:
        populate_by_name = True


@router.put("/scores")
def update_scores(payload: UpdateStudentScoresPayload):
    """Updates AI and/or DevOps track scores for a student and recalculates aggregate average using average.py."""
    if payload.aiScore is not None and (payload.aiScore < 0 or payload.aiScore > 100):
        raise HTTPException(status_code=400, detail="AI score must be between 0 and 100")
    if payload.devopsScore is not None and (payload.devopsScore < 0 or payload.devopsScore > 100):
        raise HTTPException(status_code=400, detail="DevOps score must be between 0 and 100")

    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        updated_student, updated_detail = data.update_student_scores(
            payload.registerNumber, payload.aiScore, payload.devopsScore
        )
        return {
            "status": "success",
            "message": f"Successfully updated scores for {updated_student['name']}",
            "registerNumber": updated_student["registerNumber"],
            "RegNumber": updated_student["registerNumber"],
            "aiScore": updated_student["aiScore"],
            "AIScore": updated_student["aiScore"],
            "devopsScore": updated_student["devopsScore"],
            "DevOpsScore": updated_student["devopsScore"],
            "averageScore": updated_student["average"],
            "AverageScore": updated_student["average"],
            "aiScoreDisplay": updated_student["aiScoreDisplay"],
            "devopsScoreDisplay": updated_student["devopsScoreDisplay"],
            "averageDisplay": updated_student["averageDisplay"],
            "studentStatus": updated_student["status"],
            "statusColor": updated_student["statusColor"],
            "student": updated_student,
            "detail": updated_detail,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class WatchlistTogglePayload(BaseModel):
    registerNumber: str
    watchlist: Optional[bool] = None


@router.get("/roster")
def get_roster():
    """Returns the entire student roster with exact original MongoDB scores and metadata."""
    print("[student] rendering Student Roster page")
    return {"students": data.STUDENT_ROSTER}


@router.get("/roster/{register_number}")
def get_roster_student_detail(register_number: str):
    print(f"[student] rendering Student Profile Detail page (register_number={register_number!r})")
    student = data.find_roster_student(register_number)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    detail = data.build_roster_student_detail(student)
    return {"student": student, "detail": detail}


@router.post("/watchlist/toggle")
def toggle_watchlist(payload: WatchlistTogglePayload):
    """Toggles or sets watchlist status for a student in MongoDB and memory."""
    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    new_state = (not student.get("onWatchlist", False)) if payload.watchlist is None else payload.watchlist
    data.set_student_watchlist(payload.registerNumber, new_state)

    return {
        "status": "success",
        "registerNumber": payload.registerNumber,
        "onWatchlist": new_state,
        "watchlistCount": len(data.get_watchlist_students()),
        "message": f"Student {student['name']} {'added to' if new_state else 'removed from'} watchlist",
    }


class StudentStatusPayload(BaseModel):
    registerNumber: str
    status: str


@router.post("/status")
@router.put("/status")
def update_status(payload: StudentStatusPayload):
    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    norm_status = "Selected" if payload.status.strip().lower() in ("selected", "select") else "Rejected"
    data.set_student_status(payload.registerNumber, norm_status)
    all_data = data.get_track_data("All tracks")
    return {
        "status": "success",
        "registerNumber": payload.registerNumber,
        "studentStatus": norm_status,
        "counts": all_data.get("panelCounts", {"selected": 0, "rejected": 0}),
        "message": f"Student {student['name']} status updated to {norm_status}",
    }


class AddTopicScorePayload(BaseModel):
    registerNumber: str
    track: str = "AI"
    topic: str
    score: float


@router.post("/topic-score")
def add_topic_score(payload: AddTopicScorePayload):
    """Adds or updates a topic score for a student in AI or DevOps track."""
    if not payload.topic or not payload.topic.strip():
        raise HTTPException(status_code=400, detail="Topic name cannot be empty")
    if payload.score < 0 or payload.score > 100:
        raise HTTPException(status_code=400, detail="Score must be between 0 and 100")
    
    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        updated_student, updated_detail = data.add_topic_score_for_student(
            payload.registerNumber, payload.track, payload.topic, payload.score
        )
        return {
            "status": "success",
            "message": f"Successfully added topic score '{payload.topic}': {payload.score} to {payload.track} Track",
            "student": updated_student,
            "detail": updated_detail,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class UpdateTopicScorePayload(BaseModel):
    registerNumber: str
    oldTrack: str
    oldTopic: str
    newTrack: str
    newTopic: str
    newScore: float


class DeleteTopicScorePayload(BaseModel):
    registerNumber: str
    track: str
    topic: str


@router.put("/topic-score")
def update_topic_score(payload: UpdateTopicScorePayload):
    """Updates an existing topic score for a student."""
    if not payload.newTopic or not payload.newTopic.strip():
        raise HTTPException(status_code=400, detail="Topic name cannot be empty")
    if payload.newScore < 0 or payload.newScore > 100:
        raise HTTPException(status_code=400, detail="Score must be between 0 and 100")

    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        updated_student, updated_detail = data.update_topic_score_for_student(
            payload.registerNumber,
            payload.oldTrack,
            payload.oldTopic,
            payload.newTrack,
            payload.newTopic,
            payload.newScore,
        )
        return {
            "status": "success",
            "message": f"Successfully updated topic score '{payload.newTopic}'",
            "student": updated_student,
            "detail": updated_detail,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/topic-score")
def delete_topic_score(payload: DeleteTopicScorePayload):
    """Deletes a topic score for a student."""
    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        updated_student, updated_detail = data.delete_topic_score_for_student(
            payload.registerNumber, payload.track, payload.topic
        )
        return {
            "status": "success",
            "message": f"Successfully deleted topic '{payload.topic}'",
            "student": updated_student,
            "detail": updated_detail,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class AddWeightPayload(BaseModel):
    registerNumber: str
    assessmentType: str
    weightage: float


class DeleteWeightPayload(BaseModel):
    registerNumber: str
    assessmentType: str


@router.get("/weights/{register_number}")
def get_student_weights(register_number: str):
    """Live-reads a student's weights (and their total) straight from MongoDB."""
    summary = data.get_student_weights_summary(register_number)
    if summary is None:
        raise HTTPException(status_code=404, detail="Student not found or database unavailable")
    return summary


@router.post("/weight")
def add_weight(payload: AddWeightPayload):
    """Adds or updates an assessment-type weightage entry for a student."""
    if not payload.assessmentType or not payload.assessmentType.strip():
        raise HTTPException(status_code=400, detail="Assessment type cannot be empty")
    if payload.weightage < 0 or payload.weightage > 100:
        raise HTTPException(status_code=400, detail="Weightage must be between 0 and 100")

    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        updated_student, updated_detail = data.add_weight_for_student(
            payload.registerNumber, payload.assessmentType, payload.weightage
        )
        return {
            "status": "success",
            "message": f"Successfully added weightage '{payload.assessmentType}': {payload.weightage}",
            "student": updated_student,
            "detail": updated_detail,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/weight")
def delete_weight(payload: DeleteWeightPayload):
    """Deletes an assessment-type weightage entry for a student."""
    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        updated_student, updated_detail = data.delete_weight_for_student(
            payload.registerNumber, payload.assessmentType
        )
        return {
            "status": "success",
            "message": f"Successfully deleted weightage '{payload.assessmentType}'",
            "student": updated_student,
            "detail": updated_detail,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class UpdateFeedbackPayload(BaseModel):
    registerNumber: str
    note: str
    feedbackFrom: Optional[str] = None


@router.post("/feedback")
def update_feedback(payload: UpdateFeedbackPayload):
    """Sets/overwrites the mentor feedback note for a student."""
    if not payload.note or not payload.note.strip():
        raise HTTPException(status_code=400, detail="Feedback note cannot be empty")

    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        updated_student, updated_detail = data.update_feedback_for_student(
            payload.registerNumber, payload.note, payload.feedbackFrom
        )
        return {
            "status": "success",
            "message": "Successfully updated mentor feedback",
            "student": updated_student,
            "detail": updated_detail,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/dashboard")
def get_my_dashboard():
    """The logged-in student's own dashboard (score trend, module scores,
    deadlines, mentor feedback) — kept for parity with the original mock."""
    print("[student] rendering My Dashboard page")
    return {
        "profile": data.STUDENT_PROFILE,
        "profileMeta": data.CATEGORY_META[data.STUDENT_PROFILE["status"]],
        "stats": data.STUDENT_STATS,
        "scoreTrend": data.SCORE_TREND,
        "moduleScores": data.MODULE_SCORES,
        "deadlines": data.UPCOMING_DEADLINES,
        "feedback": data.MENTOR_FEEDBACK,
    }

