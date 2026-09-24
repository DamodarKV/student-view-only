"""
insert_mongo.py
---------------
MongoDB Atlas connection, student data CRUD, and a one-time seed utility.
MongoDB Atlas is the sole data store for reads/writes at runtime — nothing
is saved to a local JSON file. `push_students_to_mongo()` / the `__main__`
block are the one exception: they exist purely to *seed* Atlas from a local
JSON file you provide, which is loading data, not saving it.
"""
import json
import logging
import threading
from pathlib import Path
from urllib.parse import quote_plus
import re
from typing import List, Dict, Any, Tuple, Optional

import average

try:
    import certifi
except ImportError:
    certifi = None

from pymongo import MongoClient, ASCENDING
from pymongo.server_api import ServerApi

logger = logging.getLogger("studentview.mongo")

# ============================================================
# CONFIGURATION
# ============================================================
USERNAME = "damodarkvlearning_db_user"
PASSWORD = "decode3103"
CLUSTER_HOST = "cluster0.vsg22u7.mongodb.net"
DB_NAME = "Cluster0"
COLLECTION_NAME = "students"

BASE_DIR = Path(__file__).resolve().parent
JSON_FILE_PATH = BASE_DIR / "students_output_accuracy2.json"  # seed source only — never written to
INSTRUCTOR_COLLECTION_NAME = "insturctor"


def get_mongo_uri() -> str:
    user = quote_plus(USERNAME)
    pwd = quote_plus(PASSWORD)
    return f"mongodb+srv://{user}:{pwd}@{CLUSTER_HOST}/?retryWrites=true&w=majority&appName=Cluster0"


# ============================================================
# CONNECTION POOLING
# ------------------------------------------------------------
# The single biggest source of production latency here was that every
# helper below used to call get_mongo_client() and client.close() on every
# call — i.e. a brand new MongoClient (fresh TLS handshake + SRV/DNS lookup
# + Atlas cluster negotiation, commonly 200ms-1s+) was paid on *every single
# request*, including simple reads. A MongoClient is thread-safe and already
# manages its own internal connection pool, so it is meant to be created
# once per process and reused — never per-call. We do exactly that here:
# a single lazily-created client, guarded by a lock so concurrent request
# threads/workers don't race to create duplicates.
# ============================================================
_client: Optional[MongoClient] = None
_client_lock = threading.Lock()
_indexes_ensured = False


def get_mongo_client() -> MongoClient:
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is None:
            kwargs: Dict[str, Any] = {
                "server_api": ServerApi("1"),
                "serverSelectionTimeoutMS": 5000,
                "connectTimeoutMS": 5000,
                "socketTimeoutMS": 10000,
                # Keep a warm pool of reusable sockets instead of opening a
                # new one per request; maxIdleTimeMS avoids hoarding idle
                # connections between bursts of traffic.
                "minPoolSize": 3,
                "maxPoolSize": 50,
                "maxIdleTimeMS": 60000,
                "retryWrites": True,
            }
            if certifi:
                kwargs["tlsCAFile"] = certifi.where()

            _client = MongoClient(get_mongo_uri(), **kwargs)
            _ensure_indexes(_client)
        return _client


def close_mongo_client() -> None:
    """Call on app shutdown to release pooled sockets cleanly."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def _ensure_indexes(client: MongoClient) -> None:
    """
    Best-effort, run-once index setup. RegNumber lookups happen on nearly
    every read/write in this module but had no index at all, so every
    find_one({"RegNumber": ...}) — and worse, every case-insensitive
    "$regex" fallback in the same $or — was a full collection scan. A
    regular ascending index lets the exact-match branch (the common case,
    since RegNumbers are stored consistently) resolve in O(log n) instead
    of scanning every document.
    """
    global _indexes_ensured
    if _indexes_ensured:
        return
    try:
        coll = client[DB_NAME][COLLECTION_NAME]
        coll.create_index([("RegNumber", ASCENDING)], name="regnumber_idx", background=True)
        _indexes_ensured = True
    except Exception as exc:
        print(f"[Database] Notice: Could not ensure indexes ({exc.__class__.__name__}).")


def fetch_students_data() -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetches student data from MongoDB Atlas. Returns: (students_list, source_name).
    On failure, returns ([], "None") — no local fallback.
    """
    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        docs = list(coll.find({}, {"_id": 0}))

        if docs:
            ensure_default_student_statuses(docs)
            print(f"[MongoDB Atlas] Retrieved {len(docs)} student records from {DB_NAME}.{COLLECTION_NAME}")
            return docs, "MongoDB Atlas"
    except Exception as exc:
        print(f"[MongoDB Atlas] Notice: Atlas connection not available ({exc.__class__.__name__}).")

    return [], "None"


def fetch_single_student(register_number: str) -> Optional[Dict[str, Any]]:
    """
    Fetches just one student's document by RegNumber. Used after a
    single-record write (topic score / assignment / watchlist change) so
    callers can refresh that one student in memory instead of paying for a
    full collection fetch of every student the way reload of the whole
    roster does.
    """
    reg_clean = register_number.strip()
    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        doc = coll.find_one(
            {"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]},
            {"_id": 0},
        )
        return doc
    except Exception as exc:
        print(f"[Database] Notice: Could not fetch student {register_number} ({exc.__class__.__name__}).")
        return None


def update_student_watchlist(reg_number: str, on_watchlist: bool) -> bool:
    """
    Updates WatchListStatus for a student in MongoDB Atlas. TrackStatus is NOT modified.
    """
    status_val = "Watchlist" if on_watchlist else ""
    reg_clean = reg_number.strip()

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        res = coll.update_one(
            {"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]},
            {"$set": {"WatchListStatus": status_val}}
        )
        if res.matched_count > 0:
            print(f"[MongoDB Atlas] Updated WatchListStatus='{status_val}' for {reg_number} in Atlas")
            return True
    except Exception as exc:
        print(f"[MongoDB Atlas] Notice: Atlas update skipped ({exc.__class__.__name__}).")

    return False


def update_student_status(reg_number: str, status: str) -> bool:
    """
    Updates TrackStatus for a student in MongoDB Atlas.
    """
    norm_status = "Selected" if str(status).strip().lower() in ("selected", "select") else (
        "Rejected" if str(status).strip().lower() in ("rejected", "reject") else str(status).strip()
    )
    reg_clean = reg_number.strip()

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        res = coll.update_one(
            {"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]},
            {"$set": {"TrackStatus": norm_status}}
        )
        if res.matched_count > 0:
            print(f"[MongoDB Atlas] Updated TrackStatus='{norm_status}' for {reg_number} in Atlas")
            return True
    except Exception as exc:
        print(f"[MongoDB Atlas] Notice: Atlas status update skipped ({exc.__class__.__name__}).")

    return False


def ensure_default_student_statuses(docs: List[Dict[str, Any]]) -> None:
    """
    If no students in MongoDB Atlas have TrackStatus set yet, initialize
    Selected (Performance >= 50) and Rejected (Performance < 50) directly in Atlas.
    """
    if not docs:
        return
    has_status = any(d.get("TrackStatus") and str(d.get("TrackStatus")).strip() for d in docs)
    if has_status:
        return

    print("[MongoDB Atlas] Initializing default TrackStatus for students based on Performance >= 50...")
    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        for doc in docs:
            raw_weights = doc.get("Weightage")
            total_w = 0.0
            if isinstance(raw_weights, list):
                for w in raw_weights:
                    if isinstance(w, dict):
                        try:
                            total_w += float(w.get("Weightage", 0))
                        except (TypeError, ValueError):
                            pass
            status = "Selected" if total_w >= 50.0 else "Rejected"
            doc["TrackStatus"] = status
            reg = doc.get("RegNumber")
            if reg:
                coll.update_one(
                    {"$or": [{"RegNumber": reg}, {"RegNumber": {"$regex": f"^{reg}$", "$options": "i"}}]},
                    {"$set": {"TrackStatus": status}}
                )
        print("[MongoDB Atlas] Successfully initialized TrackStatus in Atlas.")
    except Exception as exc:
        print(f"[MongoDB Atlas] Notice: Default TrackStatus initialization skipped ({exc}).")



def update_student_track_scores(reg_number: str, ai_score: Optional[float], devops_score: Optional[float], aggregate_score: Optional[float]) -> bool:
    """
    Updates AverageScore (AI, DevOps, and Aggregate) for a student in MongoDB Atlas.
    Leaves the Scores topic dictionary intact.
    """
    reg_clean = reg_number.strip()
    update_fields = {}
    if ai_score is not None:
        update_fields["AverageScore.AI"] = round(float(ai_score), 2)
    if devops_score is not None:
        update_fields["AverageScore.DevOps"] = round(float(devops_score), 2)
    if aggregate_score is not None:
        update_fields["AverageScore.Aggregate"] = round(float(aggregate_score), 2)

    if not update_fields:
        return False

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        res = coll.update_one(
            {"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]},
            {"$set": update_fields}
        )
        if res.matched_count > 0:
            print(f"[MongoDB Atlas] Updated track scores for {reg_number}: {update_fields}")
            return True
    except Exception as exc:
        print(f"[MongoDB Atlas] Notice: Atlas update track scores skipped ({exc.__class__.__name__}).")

    return False


def add_student_topic_score(reg_number: str, track: str, topic_name: str, score: float) -> bool:
    """
    Adds or updates a topic score under Scores.[AI|DevOps] for a student in MongoDB Atlas.
    Recalculates track average and aggregate score using average.py fixed denominators.
    """
    reg_clean = reg_number.strip()
    track_key = "DevOps" if "devops" in track.lower() else "AI"
    topic_clean = topic_name.strip()
    score_val = round(float(score), 2)

    def _recalc_doc_scores(doc: dict):
        if "Scores" not in doc or not isinstance(doc["Scores"], dict):
            doc["Scores"] = {"AI": {}, "DevOps": {}}
        if track_key not in doc["Scores"] or not isinstance(doc["Scores"][track_key], dict):
            doc["Scores"][track_key] = {}

        doc["Scores"][track_key][topic_clean] = score_val

        if "AverageScore" not in doc or not isinstance(doc["AverageScore"], dict):
            doc["AverageScore"] = {}

        has_devops = (
            track_key == "DevOps"
            or (doc.get("AverageScore", {}).get("DevOps") is not None and str(doc.get("AverageScore", {}).get("DevOps")).strip() != "")
            or bool(doc.get("Scores", {}).get("DevOps"))
        )

        calc_res = average.calculate_student_averages_from_topics(doc["Scores"], has_devops=has_devops)
        ai_avg = calc_res["AI"]
        devops_avg = calc_res["DevOps"]
        agg = calc_res["Aggregate"]

        doc["AverageScore"]["AI"] = ai_avg
        if devops_avg is not None:
            doc["AverageScore"]["DevOps"] = devops_avg
        doc["AverageScore"]["Aggregate"] = agg

        return ai_avg, devops_avg, agg

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        doc = coll.find_one({"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]})
        if doc:
            ai_avg, devops_avg, agg = _recalc_doc_scores(doc)
            set_dict = {
                f"Scores.{track_key}.{topic_clean}": score_val,
                "AverageScore.AI": ai_avg,
                "AverageScore.Aggregate": agg,
            }
            if devops_avg is not None:
                set_dict["AverageScore.DevOps"] = devops_avg
            coll.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": set_dict,
                    "$pull": {f"AssignmentTopics.{track_key}": topic_clean},
                }
            )
            print(f"[Database] Updated topic '{topic_clean}'={score_val} for {reg_number} in MongoDB Atlas students collection")
            return True
    except Exception as exc:
        print(f"[Database] Notice: Could not update MongoDB Atlas topic score ({exc.__class__.__name__}).")

def add_exam_to_all_students(topic_name: str, track_name: str) -> Tuple[bool, str, int]:
    """
    Creates a new topic/exam for the specified track across ALL student documents
    in the MongoDB 'students' collection, initialized to None (unscored).
    Recalculates AverageScore for all students based on the newly expanded topic denominator.
    Also appends the topic to the instructor syllabus.
    Returns (success: bool, message/topic_key: str, updated_count: int).
    """
    clean_topic = topic_name.strip()
    if not clean_topic:
        return False, "Topic name cannot be empty", 0

    track_key = "DevOps" if "devops" in track_name.lower() else "AI"
    topic_key = average.to_topic_key(clean_topic)
    if not topic_key:
        return False, "Invalid topic name", 0

    # Duplicate check within the track
    existing_topics = average.get_devops_topics() if track_key == "DevOps" else average.get_ai_topics()
    norm_new = average.normalize_topic_name(topic_key)
    for t in existing_topics:
        if average.normalize_topic_name(t) == norm_new:
            return False, f"Topic already exists in {track_key} Track.", 0

    try:
        client = get_mongo_client()
        db_instance = client[DB_NAME]
        coll = db_instance[COLLECTION_NAME]

        # 1. Update all student documents with the new topic set to None (empty score)
        res = coll.update_many(
            {},
            {"$set": {f"Scores.{track_key}.{topic_key}": None}}
        )
        updated_count = res.matched_count

        # 2. Register in central average topic registry
        average.register_topic(track_key, topic_key)

        # 3. Recalculate AverageScore for all students with new denominator
        for doc in coll.find({}, {"Scores": 1, "AverageScore": 1}):
            has_devops = (
                track_key == "DevOps"
                or (doc.get("AverageScore", {}).get("DevOps") is not None and str(doc.get("AverageScore", {}).get("DevOps")).strip() != "")
                or bool(doc.get("Scores", {}).get("DevOps"))
            )
            calc_res = average.calculate_student_averages_from_topics(doc.get("Scores", {}), has_devops=has_devops)
            set_dict = {
                "AverageScore.AI": calc_res["AI"],
                "AverageScore.Aggregate": calc_res["Aggregate"],
            }
            if calc_res["DevOps"] is not None:
                set_dict["AverageScore.DevOps"] = calc_res["DevOps"]
            coll.update_one({"_id": doc["_id"]}, {"$set": set_dict})

        # 4. Also append to instructor syllabus document
        try:
            add_instructor_topic(topic_name=clean_topic, track_name=f"{track_key} Track", status="upcoming", date=None)
        except Exception as e:
            print(f"[MongoDB Atlas] Notice: Failed to append to instructor syllabus: {e}")

        print(f"[MongoDB Atlas] Added exam '{topic_key}' to all {updated_count} students in {track_key} Track")
        return True, topic_key, updated_count

    except Exception as exc:
        print(f"[MongoDB Atlas] Error adding exam to all students: {exc}")
        return False, str(exc), 0


def delete_student_topic_score(reg_number: str, track: str, topic_name: str) -> bool:
    """
    Deletes a topic score under Scores.[AI|DevOps] for a student in MongoDB Atlas.
    Recalculates track average and aggregate score.
    """
    reg_clean = reg_number.strip()
    track_key = "DevOps" if "devops" in track.lower() else "AI"
    topic_clean = topic_name.strip()

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        doc = coll.find_one({"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]})
        if not doc:
            return False

        # Locate exact key in Scores[track_key]
        target_key = None
        if "Scores" in doc and isinstance(doc["Scores"], dict) and track_key in doc["Scores"]:
            track_dict = doc["Scores"][track_key]
            if isinstance(track_dict, dict):
                norm_target = average.normalize_topic_name(topic_clean)
                for k in list(track_dict.keys()):
                    if k == topic_clean or k.strip().lower() == topic_clean.lower() or average.normalize_topic_name(k) == norm_target:
                        target_key = k
                        del track_dict[k]
                        break

        # If not found under normalized name, check fallback key conversions
        if not target_key:
            target_key = average.to_topic_key(topic_clean)
            if "Scores" in doc and isinstance(doc["Scores"], dict) and track_key in doc["Scores"]:
                if target_key in doc["Scores"][track_key]:
                    del doc["Scores"][track_key][target_key]

        if "AverageScore" not in doc or not isinstance(doc["AverageScore"], dict):
            doc["AverageScore"] = {}

        has_devops = (
            track_key == "DevOps"
            or (doc.get("AverageScore", {}).get("DevOps") is not None and str(doc.get("AverageScore", {}).get("DevOps")).strip() != "")
            or bool(doc.get("Scores", {}).get("DevOps"))
        )

        calc_res = average.calculate_student_averages_from_topics(doc.get("Scores", {}), has_devops=has_devops)
        ai_avg = calc_res["AI"]
        devops_avg = calc_res["DevOps"]
        agg = calc_res["Aggregate"]

        doc["AverageScore"]["AI"] = ai_avg
        if devops_avg is not None:
            doc["AverageScore"]["DevOps"] = devops_avg
        doc["AverageScore"]["Aggregate"] = agg

        set_dict = {
            "AverageScore.AI": ai_avg,
            "AverageScore.Aggregate": agg,
        }
        if devops_avg is not None:
            set_dict["AverageScore.DevOps"] = devops_avg

        unset_key = target_key or topic_clean
        coll.update_one(
            {"_id": doc["_id"]},
            {
                "$unset": {f"Scores.{track_key}.{unset_key}": ""},
                "$set": set_dict,
            },
        )
        print(f"[Database] Deleted topic '{unset_key}' for {reg_number} in MongoDB Atlas students collection")
        return True
    except Exception as exc:
        print(f"[Database] Notice: Could not delete Atlas topic score ({exc.__class__.__name__}): {exc}")

    return False


def add_student_weight(reg_number: str, assessment_type: str, weightage: float) -> bool:
    """
    Adds or updates an assessment-type weightage entry for a student in
    MongoDB Atlas. Stored as a list of {AssessmentType, Weightage} dicts
    under the student document's `Weightage` field. If an entry for the
    same assessment type already exists (case-insensitive), it is updated
    in place instead of duplicated.
    """
    reg_clean = reg_number.strip()
    type_clean = assessment_type.strip()
    weight_val = round(float(weightage), 2)

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        doc = coll.find_one({"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]})
        if doc:
            weights = doc.get("Weightage")
            if not isinstance(weights, list):
                weights = []

            updated = False
            for w in weights:
                if isinstance(w, dict) and (w.get("AssessmentType") or "").strip().lower() == type_clean.lower():
                    w["AssessmentType"] = type_clean
                    w["Weightage"] = weight_val
                    updated = True
                    break
            if not updated:
                weights.append({"AssessmentType": type_clean, "Weightage": weight_val})

            coll.update_one({"_id": doc["_id"]}, {"$set": {"Weightage": weights}})
            print(f"[Database] Updated weightage '{type_clean}'={weight_val} for {reg_number} in MongoDB Atlas")
            return True
    except Exception as exc:
        print(f"[Database] Notice: Could not update MongoDB Atlas weightage ({exc.__class__.__name__}).")

    return False


def delete_student_weight(reg_number: str, assessment_type: str) -> bool:
    """
    Deletes an assessment-type weightage entry for a student in MongoDB Atlas.
    """
    reg_clean = reg_number.strip()
    type_clean = assessment_type.strip()

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        doc = coll.find_one({"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]})
        if doc:
            weights = doc.get("Weightage")
            if not isinstance(weights, list):
                weights = []
            weights = [
                w for w in weights
                if not (isinstance(w, dict) and (w.get("AssessmentType") or "").strip().lower() == type_clean.lower())
            ]
            coll.update_one({"_id": doc["_id"]}, {"$set": {"Weightage": weights}})
            print(f"[Database] Deleted weightage '{type_clean}' for {reg_number} in MongoDB Atlas")
            return True
    except Exception as exc:
        print(f"[Database] Notice: Could not delete MongoDB Atlas weightage ({exc.__class__.__name__}).")

    return False


def update_student_feedback(reg_number: str, note: str, feedback_from: Optional[str] = None, date: Optional[str] = None) -> bool:
    """
    Sets/overwrites the mentor feedback note for a student in MongoDB Atlas.
    Stored under the student document's `MentorFeedback` field as
    {Note, From, Date}. `feedback_from`/`date` are optional — when omitted,
    whatever is already stored for them is preserved (only Note changes).
    """
    reg_clean = reg_number.strip()
    note_clean = note.strip()

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        doc = coll.find_one({"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]})
        if doc:
            existing = doc.get("MentorFeedback")
            existing = existing if isinstance(existing, dict) else {}
            updated = {
                "Note": note_clean,
                "From": (feedback_from.strip() if feedback_from else existing.get("From", "")),
                "Date": (date.strip() if date else existing.get("Date", "")),
            }
            coll.update_one({"_id": doc["_id"]}, {"$set": {"MentorFeedback": updated}})
            print(f"[Database] Updated mentor feedback for {reg_number} in MongoDB Atlas")
            return True
    except Exception as exc:
        print(f"[Database] Notice: Could not update MongoDB Atlas mentor feedback ({exc.__class__.__name__}).")

    return False


def update_student_topic_score(
    reg_number: str,
    old_track: str,
    old_topic: str,
    new_track: str,
    new_topic: str,
    new_score: float,
) -> bool:
    """
    Updates an existing topic score (or renames topic/track) for a student in MongoDB Atlas.
    """
    old_track_key = "DevOps" if "devops" in old_track.lower() else "AI"
    new_track_key = "DevOps" if "devops" in new_track.lower() else "AI"
    if old_track_key != new_track_key or old_topic.strip().lower() != new_topic.strip().lower():
        delete_student_topic_score(reg_number, old_track, old_topic)
    return add_student_topic_score(reg_number, new_track, new_topic, new_score)


def fetch_instructor_data() -> Tuple[Dict[str, Any], str]:
    """
    Fetches instructor data from MongoDB Atlas (collection: insturctor or instructor).
    Returns (data_dict, source_name). On failure, returns ({}, "None").
    """
    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        for cname in [INSTRUCTOR_COLLECTION_NAME, "instructor"]:
            if cname in db.list_collection_names():
                doc = db[cname].find_one({}, {"_id": 0})
                if doc and ("ai_track" in doc or "devops_track" in doc):
                    print(f"[MongoDB Atlas] Retrieved instructor data from {DB_NAME}.{cname}")
                    return doc, "MongoDB Atlas"
    except Exception as exc:
        print(f"[MongoDB Atlas] Notice: Atlas instructor fetch skipped ({exc.__class__.__name__}).")

    return {}, "None"


def _track_to_key(track_name: str) -> str:
    t = track_name.strip().lower()
    if "devops" in t:
        return "devops_track"
    return "ai_track"


def update_instructor_topic_status(topic_name: str, track_name: str, new_status: str, completed_date: Optional[str] = None) -> bool:
    """
    Updates the status of a topic in MongoDB Atlas ('insturctor' & 'instructor').
    """
    track_key = _track_to_key(track_name)
    status_lower = new_status.strip().lower().replace(" ", "_")
    topic_clean = topic_name.strip().lower()

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        updated = False
        for cname in [INSTRUCTOR_COLLECTION_NAME, "instructor"]:
            if cname in db.list_collection_names():
                doc = db[cname].find_one({})
                if doc and track_key in doc:
                    topics = doc[track_key].get("topics", [])
                    matched = False
                    for t in topics:
                        if t.get("topic", "").strip().lower() == topic_clean:
                            t["status"] = status_lower
                            if completed_date is not None:
                                t["date_completed"] = completed_date
                            matched = True
                            break
                    if matched:
                        db[cname].update_one({"_id": doc["_id"]}, {"$set": {f"{track_key}.topics": topics}})
                        print(f"[MongoDB Atlas] Updated topic '{topic_name}' status to '{new_status}' in {cname}")
                        updated = True
        return updated
    except Exception as exc:
        print(f"[MongoDB Atlas] Notice: Could not update MongoDB topic status directly ({exc.__class__.__name__}).")

    return False


def add_instructor_topic(topic_name: str, track_name: str, status: str, date: Optional[str] = None) -> bool:
    """
    Appends a new topic to the instructor document in MongoDB Atlas.
    """
    track_key = _track_to_key(track_name)
    status_lower = status.strip().lower().replace(" ", "_")
    new_entry = {
        "topic": topic_name.strip(),
        "status": status_lower,
        "date_completed": date or ""
    }

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        updated = False
        for cname in [INSTRUCTOR_COLLECTION_NAME, "instructor"]:
            if cname in db.list_collection_names():
                db[cname].update_one(
                    {},
                    {"$push": {f"{track_key}.topics": new_entry}}
                )
                print(f"[MongoDB Atlas] Added new topic '{topic_name}' to {cname}")
                updated = True
        return updated
    except Exception as exc:
        print(f"[MongoDB Atlas] Notice: Could not append topic to Atlas ({exc.__class__.__name__}).")

    return False


def fetch_student_assignments_from_mongo(register_number: str) -> List[Dict[str, Any]]:
    """
    Fetches assignments for a specific student from the `Assignment` field on
    their document in the `students` collection in MongoDB Atlas. No local fallback.
    """
    reg_clean = register_number.strip().lower()

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {"RegNumber": register_number.strip()},
                    {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}},
                ]
            },
            {"_id": 0, "Assignment": 1},
        )
        if doc and isinstance(doc.get("Assignment"), list):
            print(f"[Database] Retrieved {len(doc['Assignment'])} assignments for {register_number} from students collection (Assignment field)")
            return doc["Assignment"]
    except Exception as exc:
        print(f"[Database] Notice: Atlas assignment fetch skipped ({exc.__class__.__name__}).")

    return []


def sync_assignment_to_student_record(reg_number: str, track: str, topic_name: str, score: Optional[float] = None) -> bool:
    """
    Syncs an assignment topic into the student's Scores.[AI|DevOps] dictionary
    in MongoDB Atlas.

    Also records the topic under AssignmentTopics.[AI|DevOps] so it can be
    told apart from a genuine assessment score later. An assignment can be
    created (or edited) WITH a score already attached, so a non-null value in
    Scores alone is not proof that a topic is a real assessment — only
    add_student_topic_score() (the actual "Add Topic Score" workflow, which
    also mirrors into the 'assesement'/'assessment' collections) represents a
    genuine assessment. Downstream (data.py's module bar-graph charts) reads
    this tag to keep assignment-sourced values out of the "real assessment"
    bar charts.
    """
    reg_clean = reg_number.strip()
    track_key = "DevOps" if "devops" in track.lower() else "AI"
    topic_clean = topic_name.strip()
    score_val = round(float(score), 2) if (score is not None and score != "") else None

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        doc = coll.find_one({"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]})
        if doc:
            coll.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {f"Scores.{track_key}.{topic_clean}": score_val},
                    "$addToSet": {f"AssignmentTopics.{track_key}": topic_clean},
                }
            )
            print(f"[Database] Synced assignment '{topic_clean}' (Score: {score_val}) to student {reg_number} in Atlas")
            return True
    except Exception as exc:
        print(f"[Database] Notice: Could not sync assignment to Atlas ({exc.__class__.__name__}).")

    return False


def update_assignment_in_mongo(
    reg_number: str,
    assignment_id: Optional[str],
    old_track: str,
    old_topic: str,
    new_track: str,
    new_topic: str,
    new_score: Optional[float] = None,
    new_status: Optional[str] = None,
) -> bool:
    """
    Updates an existing entry in the student's `Assignment` array (matched by
    assignmentId, falling back to old track/topic) and re-syncs the topic into
    Scores.[AI|DevOps] via sync_assignment_to_student_record — which keeps the
    topic tagged under AssignmentTopics.[AI|DevOps]. This is deliberately NOT
    routed through add_student_topic_score(): editing an assignment's score
    here must NOT promote it into a genuine assessment score, or it would
    incorrectly start showing up in the module bar-graph charts, which are
    meant to show only real assessments, not assignment data.
    """
    reg_clean = reg_number.strip()
    old_track_key = "DevOps" if "devops" in old_track.lower() else "AI"
    new_track_key = "DevOps" if "devops" in new_track.lower() else "AI"
    old_topic_clean = old_topic.strip()
    new_topic_clean = new_topic.strip()
    matched = False

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        doc = coll.find_one({"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]})
        if not doc:
            return False

        assignments = doc.get("Assignment") or []
        for a in assignments:
            same_id = assignment_id and a.get("assignmentId") == assignment_id
            same_topic = (
                not assignment_id
                and (a.get("topic") or "").strip().lower() == old_topic_clean.lower()
                and (a.get("track") or "AI") == old_track_key
            )
            if same_id or same_topic:
                a["topic"] = new_topic_clean
                a["track"] = new_track_key
                if new_status is not None:
                    a["status"] = new_status
                if new_score is not None:
                    a["score"] = new_score
                matched = True
                break

        if matched:
            coll.update_one({"_id": doc["_id"]}, {"$set": {"Assignment": assignments}})

        # If the topic/track changed, remove the stale Scores/AssignmentTopics
        # entry left under the old key before re-syncing under the new one.
        if old_track_key != new_track_key or old_topic_clean.lower() != new_topic_clean.lower():
            coll.update_one(
                {"_id": doc["_id"]},
                {
                    "$unset": {f"Scores.{old_track_key}.{old_topic_clean}": ""},
                    "$pull": {f"AssignmentTopics.{old_track_key}": old_topic_clean},
                },
            )

        print(f"[Database] Updated assignment '{new_topic_clean}' for {reg_number} in Atlas (Assignment field)")
    except Exception as exc:
        print(f"[Database] Notice: Could not update assignment in Atlas ({exc.__class__.__name__}).")
        matched = False

    # Re-sync Scores + AssignmentTopics under the (possibly new) track/topic,
    # tagging it as an assignment-sourced value, not a genuine assessment.
    sync_assignment_to_student_record(reg_number, new_track_key, new_topic_clean, new_score)
    return matched


def delete_assignment_in_mongo(reg_number: str, assignment_id: Optional[str], track: str, topic: str) -> bool:
    """
    Removes an entry from the student's `Assignment` array and clears the
    matching Scores.[AI|DevOps] / AssignmentTopics.[AI|DevOps] entry, so a
    deleted assignment stops appearing anywhere on the dashboard, including
    the module bar-graph charts.
    """
    reg_clean = reg_number.strip()
    track_key = "DevOps" if "devops" in track.lower() else "AI"
    topic_clean = topic.strip()

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        doc = coll.find_one({"$or": [{"RegNumber": reg_clean}, {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}}]})
        if not doc:
            return False

        assignments = doc.get("Assignment") or []
        remaining = [
            a for a in assignments
            if not (
                (assignment_id and a.get("assignmentId") == assignment_id)
                or (
                    not assignment_id
                    and (a.get("topic") or "").strip().lower() == topic_clean.lower()
                    and (a.get("track") or "AI") == track_key
                )
            )
        ]

        coll.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {"Assignment": remaining},
                "$unset": {f"Scores.{track_key}.{topic_clean}": ""},
                "$pull": {f"AssignmentTopics.{track_key}": topic_clean},
            },
        )
        for ass_coll_name in ["assesement", "assessment"]:
            db[ass_coll_name].delete_many({"RegNumber": reg_clean, "Track": track_key, "Topic": topic_clean})
        print(f"[Database] Deleted assignment '{topic_clean}' for {reg_number} in Atlas (Assignment field)")
        return True
    except Exception as exc:
        print(f"[Database] Notice: Could not delete assignment in Atlas ({exc.__class__.__name__}).")

    return False


def save_assignments_to_mongo(assignments_list: List[Dict[str, Any]]) -> bool:
    """
    Saves new assignment records into each student's own document in the
    `students` collection, under an `Assignment` field (list), matched by
    RegNumber. No separate `assignments` collection is used.
    Also syncs each topic to the student's Scores dict in Atlas.
    """
    if not assignments_list:
        return True

    def _clean_item(item: dict, student_reg: str) -> dict:
        item_copy = dict(item)
        item_copy.pop("_id", None)
        if "studentRegisterNumber" not in item_copy:
            item_copy["studentRegisterNumber"] = student_reg
        return item_copy

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in assignments_list:
        reg = item.get("studentRegisterNumber") or item.get("regNo") or ""
        reg_clean = reg.strip()
        if reg_clean:
            grouped.setdefault(reg_clean, []).append(item)

    saved_ok = False
    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        for reg_no, new_items in grouped.items():
            reg_clean = reg_no.lower()
            query = {
                "$or": [
                    {"RegNumber": reg_no},
                    {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}},
                ]
            }
            student_doc = coll.find_one(query)

            existing_asgs = []
            if student_doc and isinstance(student_doc.get("Assignment"), list):
                existing_asgs = student_doc["Assignment"]

            asg_map = {a.get("assignmentId"): a for a in existing_asgs if a.get("assignmentId")}
            for item in new_items:
                asg_id = item.get("assignmentId")
                item_clean = _clean_item(item, reg_no)
                if asg_id:
                    asg_map[asg_id] = item_clean
                else:
                    existing_asgs.append(item_clean)

            updated_asg_list = list(asg_map.values()) if asg_map else existing_asgs

            if student_doc:
                coll.update_one({"_id": student_doc["_id"]}, {"$set": {"Assignment": updated_asg_list}})
                print(f"[Database] Saved {len(updated_asg_list)} assignment(s) into Students collection (Assignment field) for {reg_no}")
            else:
                # No matching student record — create a minimal one so the assignment isn't lost.
                coll.update_one(query, {"$set": {"RegNumber": reg_no, "Assignment": updated_asg_list}}, upsert=True)
                print(f"[Database] Student {reg_no} not found; created a new student record with Assignment field ({len(updated_asg_list)} item(s))")
            saved_ok = True
    except Exception as exc:
        print(f"[Database] Notice: Could not save assignments to Students collection ({exc.__class__.__name__}).")

    for item in assignments_list:
        reg = item.get("studentRegisterNumber") or item.get("regNo")
        tr = item.get("track", "AI")
        top = item.get("topic")
        sc = item.get("score")
        if reg and top:
            sync_assignment_to_student_record(reg, tr, top, sc)

    return saved_ok


def push_students_to_mongo() -> Dict[str, Any]:
    """
    One-time seed utility: pushes records from a local students_output_accuracy2.json
    file (if you provide one) into MongoDB Atlas. This is the only place in the
    project that touches a local JSON file, and it only READS it as a seed
    source — nothing is ever written back to it.
    """
    if not JSON_FILE_PATH.exists():
        raise FileNotFoundError(f"Local seed JSON not found at {JSON_FILE_PATH}")

    with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    client = get_mongo_client()
    try:
        client.admin.command("ping")
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        coll.drop()
        coll.insert_many(data)
        total = coll.count_documents({})

        return {
            "status": "success",
            "total_documents": total,
            "processed": len(data),
            "message": f"Successfully pushed {len(data)} records to MongoDB Atlas ({DB_NAME}.{COLLECTION_NAME}). Total: {total}",
        }
    finally:
        client.close()


if __name__ == "__main__":
    print(f"Loading seed data from {JSON_FILE_PATH}...")
    try:
        with open(JSON_FILE_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            data = [data]
        print(f"Loaded {len(data)} student records.")
    except Exception as e:
        print(f"Failed to read {JSON_FILE_PATH}: {e}")
        exit(1)

    print("\nConnecting to MongoDB Atlas...")
    try:
        res = push_students_to_mongo()
        print("MongoDB Atlas connection SUCCESSFUL!")
        print(f"Target Collection: {DB_NAME}.{COLLECTION_NAME}")
        print(res["message"])
    except Exception as e:
        print(f"\n[!] MongoDB Operation Failed: {e}")


def fetch_student_weights(reg_number: str) -> Optional[List[Dict[str, Any]]]:
    """
    Reads ONLY the `Weightage` field for one student straight from MongoDB
    Atlas (projection - nothing else is transferred). Returns the raw list of
    {AssessmentType, Weightage} dicts ([] if the student has none), or None
    if the student was not found / the DB call failed.
    """
    reg_clean = reg_number.strip()
    try:
        client = get_mongo_client()
        coll = client[DB_NAME][COLLECTION_NAME]
        doc = coll.find_one(
            {"RegNumber": {"$regex": f"^{re.escape(reg_clean)}$", "$options": "i"}},
            {"_id": 0, "Weightage": 1},
        )
        if doc is None:
            return None
        weights = doc.get("Weightage")
        return weights if isinstance(weights, list) else []
    except Exception as exc:
        print(f"[Database] Notice: Could not fetch weights for {reg_number} ({exc.__class__.__name__}).")
        return None
