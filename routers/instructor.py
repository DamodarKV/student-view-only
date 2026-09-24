"""Instructor overview API — powers the 'Instructor' tab of the dashboard."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import data

router = APIRouter(prefix="/api/instructor", tags=["instructor"])


@router.get("/overview")
def get_overview(track: str = "All tracks"):
    print(f"[instructor] rendering Instructor page (track={track!r})")
    if track not in data.TRACK_FILTER_OPTIONS:
        raise HTTPException(status_code=404, detail="Unknown track")

    topics = data.get_topics(track)
    show_track_column = track == "All tracks"

    total_topics = len(topics)
    completed = total_topics
    in_progress = 0
    upcoming = 0

    stats = [
        {"key": "syllabus", "label": "Syllabus completion", "value": "100", "unit": "%", "icon": "CheckCircle2", "fill": 100},
        {"key": "completed", "label": "Topics completed", "value": str(completed), "unit": f"of {total_topics}", "icon": "ClipboardCheck"},
        {"key": "inProgress", "label": "Topics in progress", "value": "0", "unit": "topics", "icon": "ClipboardList", "tone": "amber"},
        {"key": "upcoming", "label": "Topics upcoming", "value": "0", "unit": "topics", "icon": "Calendar"},
    ]

    topics_view = [
        {
            "topic": t["topic"],
            "track": t["track"],
            "status": "Completed",
            "statusMeta": {"label": "Completed", "color": "#3FBFA6"},
        }
        for t in topics
    ]

    return {
        "track": track,
        "showTrackColumn": show_track_column,
        "stats": stats,
        "topics": topics_view,
        "trackOptions": data.TRACK_OPTIONS,
        "statusOptions": ["Completed"],
    }


class CreateExamPayload(BaseModel):
    topic: str
    track: str


@router.post("/exams")
def create_exam(payload: CreateExamPayload):
    """
    Creates a new topic/exam for the specified track across ALL students
    in the students MongoDB collection with unassigned/empty score.
    """
    clean_topic = payload.topic.strip() if payload.topic else ""
    if not clean_topic:
        raise HTTPException(status_code=400, detail="Topic name cannot be empty")

    clean_track = payload.track.strip() if payload.track else ""
    if not clean_track:
        raise HTTPException(status_code=400, detail="Track is required")

    # Validate track
    track_key = "DevOps" if "devops" in clean_track.lower() else "AI"
    if "devops" not in clean_track.lower() and "ai" not in clean_track.lower():
        raise HTTPException(status_code=400, detail="Invalid track. Must be AI Track or DevOps Track.")

    try:
        result = data.create_exam_for_all_students(clean_topic, track_key)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class NewTopic(BaseModel):
    topic: str = Field(..., min_length=1)
    track: str
    status: str
    date: Optional[str] = None


@router.post("/topics")
def create_topic(payload: NewTopic):
    if payload.track not in data.TRACK_OPTIONS:
        raise HTTPException(status_code=400, detail="Unknown track")
    if payload.status not in data.STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="Unknown status")
    date = None if payload.status == "Upcoming" else (payload.date or None)
    new_topic = data.add_topic(payload.topic.strip(), payload.track, payload.status, date)
    return {
        "topic": new_topic["topic"],
        "track": new_topic["track"],
        "status": new_topic["status"],
        "statusMeta": data.TOPIC_STATUS_META[new_topic["status"]],
        "date": data.format_topic_date(new_topic["date"]),
    }




