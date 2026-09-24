"""Assignment API — endpoints for creating and querying student assignments."""
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import data

router = APIRouter(prefix="/api/assignments", tags=["assignments"])


class SingleAssignmentPayload(BaseModel):
    topic: str = Field(..., min_length=1)
    track: str = Field(..., description="AI or DevOps")
    studentRegisterNumber: Optional[str] = None
    status: Optional[str] = "PENDING"
    score: Optional[float] = None


class CreateAssignmentsRequest(BaseModel):
    assignments: List[SingleAssignmentPayload]
    studentRegisterNumbers: Optional[List[str]] = None


class UpdateAssignmentPayload(BaseModel):
    registerNumber: str
    assignmentId: Optional[str] = None
    oldTrack: str
    oldTopic: str
    newTrack: str
    newTopic: str
    newScore: Optional[float] = None
    newStatus: Optional[str] = None


class DeleteAssignmentPayload(BaseModel):
    registerNumber: str
    assignmentId: Optional[str] = None
    track: str
    topic: str


@router.get("/student/{register_number}")
def get_student_assignments(register_number: str, track: Optional[str] = None):
    """
    Returns all assignments associated with a specific student register number.
    Optionally filterable by track (AI / DevOps).
    """
    if not register_number or not register_number.strip():
        raise HTTPException(status_code=400, detail="Student register number is required")

    try:
        assignments = data.get_student_assignments(register_number, track=track)
        return {
            "status": "success",
            "registerNumber": register_number,
            "count": len(assignments),
            "assignments": assignments,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch assignments: {str(exc)}")


@router.post("")
@router.post("/")
def create_assignments_endpoint(payload: CreateAssignmentsRequest):
    """
    Creates new assignment(s) for student(s) and saves them to MongoDB.
    Supports creating assignments for a specific student or multiple students.
    """
    to_create = []

    if payload.studentRegisterNumbers and len(payload.studentRegisterNumbers) > 0:
        for reg_no in payload.studentRegisterNumbers:
            for item in payload.assignments:
                to_create.append({
                    "topic": item.topic,
                    "track": item.track,
                    "studentRegisterNumber": reg_no,
                    "status": item.status or "PENDING",
                    "score": item.score,
                })
    else:
        for item in payload.assignments:
            reg_no = item.studentRegisterNumber
            if not reg_no or not reg_no.strip():
                raise HTTPException(status_code=400, detail="Student register number is required for each assignment")
            to_create.append({
                "topic": item.topic,
                "track": item.track,
                "studentRegisterNumber": reg_no.strip(),
                "status": item.status or "PENDING",
                "score": item.score,
            })

    if not to_create:
        raise HTTPException(status_code=400, detail="No valid assignments provided")

    try:
        results = data.create_assignments(to_create)
        return {
            "status": "success",
            "message": f"Successfully created {len(results)} assignment(s)",
            "assignments": results,
        }
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save assignments: {str(exc)}")


@router.put("")
@router.put("/")
def update_assignment_endpoint(payload: UpdateAssignmentPayload):
    """
    Updates an assignment's topic/track/score/status. Kept separate from
    /api/student/topic-score on purpose — that endpoint represents adding a
    genuine assessment score, while this one edits assignment data and must
    keep the topic tagged as assignment-sourced so it doesn't leak into the
    module bar-graph charts as a "real" assessment.
    """
    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        updated_student, updated_detail = data.update_assignment_for_student(
            payload.registerNumber,
            payload.assignmentId,
            payload.oldTrack,
            payload.oldTopic,
            payload.newTrack,
            payload.newTopic,
            payload.newScore,
            payload.newStatus,
        )
        return {
            "status": "success",
            "message": f"Successfully updated assignment '{payload.newTopic}'",
            "student": updated_student,
            "detail": updated_detail,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("")
@router.delete("/")
def delete_assignment_endpoint(payload: DeleteAssignmentPayload):
    """Deletes an assignment and its synced score/tag so it disappears everywhere, including the bar charts."""
    student = data.find_roster_student(payload.registerNumber)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        updated_student, updated_detail = data.delete_assignment_for_student(
            payload.registerNumber, payload.assignmentId, payload.track, payload.topic
        )
        return {
            "status": "success",
            "message": f"Successfully deleted assignment '{payload.topic}'",
            "student": updated_student,
            "detail": updated_detail,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
