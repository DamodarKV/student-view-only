"""
db.py
-----
Handles the MongoDB Atlas connection and all data fetch/update logic.
MongoDB Atlas is the sole data store — there is no local JSON fallback.
"""
import logging
from urllib.parse import quote_plus
from typing import List, Dict, Any, Tuple, Optional

import average

try:
    import certifi
except ImportError:
    certifi = None


logger = logging.getLogger("studentview.db")

# ============================================================
# MONGODB ATLAS CONFIGURATION
# ============================================================
USERNAME = "damodarkvlearning_db_user"
PASSWORD = "decode3103"
CLUSTER_HOST = "cluster0.vsg22u7.mongodb.net"
DB_NAME = "Cluster0"
COLLECTION_NAME = "students"
INSTRUCTOR_COLLECTION_NAME = "insturctor"


def get_mongo_uri() -> str:
    user = quote_plus(USERNAME)
    pwd = quote_plus(PASSWORD)
    return f"mongodb+srv://{user}:{pwd}@{CLUSTER_HOST}/?retryWrites=true&w=majority&appName=Cluster0"


def get_mongo_client():
    from pymongo import MongoClient
    from pymongo.server_api import ServerApi

    kwargs: Dict[str, Any] = {
        "server_api": ServerApi("1"),
        "serverSelectionTimeoutMS": 3000,
        "connectTimeoutMS": 3000,
        "socketTimeoutMS": 3000,
    }
    if certifi:
        kwargs["tlsCAFile"] = certifi.where()

    return MongoClient(get_mongo_uri(), **kwargs)


def fetch_students_data() -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetches all student documents from MongoDB Atlas.
    Returns: (students_list, source_name). On any failure, returns ([], "None").
    """
    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]
        docs = list(coll.find({}, {"_id": 0}))
        client.close()

        if docs:
            print(f"[Database] Successfully retrieved {len(docs)} student records from MongoDB Atlas ({DB_NAME}.{COLLECTION_NAME})")
            return docs, "MongoDB Atlas"
    except Exception as exc:
        print(f"[Database] Notice: MongoDB Atlas not reachable ({exc.__class__.__name__}).")

    return [], "None"


def update_student_watchlist(reg_number: str, on_watchlist: bool) -> bool:
    """
    Updates WatchListStatus for a student in MongoDB Atlas. TrackStatus is NOT modified.
    Returns True if a matching student document was updated.
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
        client.close()
        if res.matched_count > 0:
            print(f"[Database] Updated WatchListStatus='{status_val}' for {reg_number} in MongoDB Atlas")
            return True
    except Exception as exc:
        print(f"[Database] Notice: Could not update MongoDB Atlas directly ({exc.__class__.__name__}).")

    return False


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
        client.close()
        if res.matched_count > 0:
            print(f"[Database] Updated track scores for {reg_number}: {update_fields}")
            return True
    except Exception as exc:
        print(f"[Database] Notice: Could not update track scores ({exc.__class__.__name__}).")

    return False


def add_student_topic_score(reg_number: str, track: str, topic_name: str, score: float) -> bool:
    """
    Adds or updates a topic score under Scores.[AI|DevOps] for a student in MongoDB Atlas.
    Recalculates track average and aggregate score.
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
                    # This topic now has a genuine, manually-entered assessment
                    # score — no longer just an assignment placeholder, even
                    # if it started out as one.
                    "$pull": {f"AssignmentTopics.{track_key}": topic_clean},
                }
            )
            print(f"[Database] Updated topic '{topic_clean}' for {reg_number} in MongoDB Atlas students collection")
            client.close()
            return True
        client.close()
    except Exception as exc:
        print(f"[Database] Notice: Could not save Atlas topic score ({exc.__class__.__name__}).")

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
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

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

        client.close()

        # 4. Also append to instructor syllabus document
        try:
            add_instructor_topic(topic_name=clean_topic, track_name=f"{track_key} Track", status="upcoming", date=None)
        except Exception as e:
            print(f"[Database] Notice: Failed to append to instructor syllabus: {e}")

        print(f"[Database] Added exam '{topic_key}' to all {updated_count} students in {track_key} Track")
        return True, topic_key, updated_count

    except Exception as exc:
        print(f"[Database] Error adding exam to all students: {exc}")
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
            client.close()
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
        client.close()
        return True
    except Exception as exc:
        print(f"[Database] Notice: Could not delete Atlas topic score ({exc.__class__.__name__}): {exc}")

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
                    client.close()
                    print(f"[Database] Successfully retrieved instructor data from MongoDB Atlas ({DB_NAME}.{cname})")
                    return doc, "MongoDB Atlas"
        client.close()
    except Exception as exc:
        print(f"[Database] Notice: Atlas instructor fetch skipped ({exc.__class__.__name__}).")

    return {}, "None"


def _track_to_key(track_name: str) -> str:
    t = track_name.strip().lower()
    if "devops" in t:
        return "devops_track"
    return "ai_track"


def update_instructor_topic_status(topic_name: str, track_name: str, new_status: str, completed_date: Optional[str] = None) -> bool:
    """
    Updates the status of a topic in MongoDB Atlas ('insturctor' / 'instructor').
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
                        print(f"[Database] Updated topic '{topic_name}' status to '{new_status}' in Atlas ({cname})")
                        updated = True
        client.close()
        return updated
    except Exception as exc:
        print(f"[Database] Notice: Could not update MongoDB topic status directly ({exc.__class__.__name__}).")

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
                print(f"[Database] Added new topic '{topic_name}' to Atlas ({cname})")
                updated = True
        client.close()
        return updated
    except Exception as exc:
        print(f"[Database] Notice: Could not append topic to Atlas ({exc.__class__.__name__}).")

    return False


def fetch_student_assignments_from_mongo(register_number: str) -> List[Dict[str, Any]]:
    """
    Fetches assignments for a specific student. Assignments are stored as an
    `Assignments` array field directly on the student's own document in the
    main Cluster0 collection (same doc as Scores, RegNumber, etc.) — there is
    no separate assignments collection.
    """
    reg_clean = register_number.strip().lower()

    try:
        client = get_mongo_client()
        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {"$or": [
                {"RegNumber": register_number.strip()},
                {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}},
            ]},
            {"_id": 0, "Assignments": 1}
        )
        client.close()
        if doc:
            assignments = doc.get("Assignments") or []
            print(f"[Database] Retrieved {len(assignments)} assignments for {register_number} from Cluster0 ({DB_NAME}.{COLLECTION_NAME})")
            return assignments
    except Exception as exc:
        print(f"[Database] Notice: Could not fetch assignments from Cluster0 Atlas ({exc.__class__.__name__}).")

    return []


def sync_assignment_to_student_record(reg_number: str, track: str, topic_name: str, score: Optional[float] = None) -> bool:
    """
    Syncs an assignment topic into the student's Scores.[AI|DevOps] dictionary
    in MongoDB Atlas (Cluster0 collection).

    Also records the topic under AssignmentTopics.[AI|DevOps] so it can be
    told apart from a genuine assessment score later — an assignment can be
    created WITH a score already attached, so a non-null value in Scores
    alone is not enough to prove a topic is a real assessment. Only
    add_student_topic_score() (the actual "Add Topic Score" workflow, which
    also mirrors into the 'assesement'/'assessment' collections) represents
    a genuine assessment.
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
            client.close()
            return True
        client.close()
    except Exception as exc:
        print(f"[Database] Notice: Could not sync assignment to Atlas ({exc.__class__.__name__}).")

    return False


def update_assignment_in_mongo(
    reg_number: str,
    assignment_id: str,
    old_track: str,
    old_topic: str,
    new_track: str,
    new_topic: str,
    new_score: Optional[float] = None,
    new_status: Optional[str] = None,
) -> bool:
    """
    Updates an existing entry in the student's `Assignments` array (matched by
    assignmentId, falling back to old track/topic) and re-syncs the topic into
    Scores.[AI|DevOps] via sync_assignment_to_student_record — which keeps the
    topic tagged under AssignmentTopics.[AI|DevOps]. This is deliberately NOT
    the same code path as add_student_topic_score(): editing an assignment's
    score here must NOT promote it into a genuine assessment score, or it
    would incorrectly start showing up in the module bar-graph charts, which
    are meant to show only real assessments, not assignment data.
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
            client.close()
            return False

        assignments = doc.get("Assignments") or []
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
            coll.update_one({"_id": doc["_id"]}, {"$set": {"Assignments": assignments}})

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

        client.close()
        print(f"[Database] Updated assignment '{new_topic_clean}' for {reg_number} in Atlas (Assignments array)")
    except Exception as exc:
        print(f"[Database] Notice: Could not update assignment in Atlas ({exc.__class__.__name__}).")
        matched = False

    # Re-sync Scores + AssignmentTopics under the (possibly new) track/topic,
    # tagging it as an assignment-sourced value, not a genuine assessment.
    sync_assignment_to_student_record(reg_number, new_track_key, new_topic_clean, new_score)
    return matched


def delete_assignment_in_mongo(reg_number: str, assignment_id: str, track: str, topic: str) -> bool:
    """
    Removes an entry from the student's `Assignments` array and clears the
    matching Scores.[AI|DevOps] / AssignmentTopics.[AI|DevOps] entry, so a
    deleted assignment stops appearing anywhere on the dashboard.
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
            client.close()
            return False

        assignments = doc.get("Assignments") or []
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
                "$set": {"Assignments": remaining},
                "$unset": {f"Scores.{track_key}.{topic_clean}": ""},
                "$pull": {f"AssignmentTopics.{track_key}": topic_clean},
            },
        )
        for ass_coll_name in ["assesement", "assessment"]:
            db[ass_coll_name].delete_many({"RegNumber": reg_clean, "Track": track_key, "Topic": topic_clean})
        client.close()
        print(f"[Database] Deleted assignment '{topic_clean}' for {reg_number} in Atlas (Assignments array)")
        return True
    except Exception as exc:
        print(f"[Database] Notice: Could not delete assignment in Atlas ({exc.__class__.__name__}).")

    return False


def save_assignments_to_mongo(assignments_list: List[Dict[str, Any]]) -> bool:
    """
    Saves new assignment records directly onto each student's own document in
    the Cluster0 collection, under an `Assignments` array field. No separate
    assignments collection is used — every read and write for assignments
    touches only the Cluster0 collection (same doc as Scores, RegNumber, etc.).
    Also keeps the student's Scores dict in sync, same as before.
    """
    if not assignments_list:
        return True

    def _clean_item(item: dict, student_reg: str) -> dict:
        item_copy = dict(item)
        item_copy.pop("_id", None)
        if "studentRegisterNumber" not in item_copy:
            item_copy["studentRegisterNumber"] = student_reg
        return item_copy

    def _merge(existing_asgs: List[Dict[str, Any]], new_items: List[Dict[str, Any]], reg_no: str) -> List[Dict[str, Any]]:
        asg_map = {a.get("assignmentId"): a for a in existing_asgs if a.get("assignmentId")}
        for item in new_items:
            asg_id = item.get("assignmentId")
            item_clean = _clean_item(item, reg_no)
            if asg_id:
                asg_map[asg_id] = item_clean
            else:
                existing_asgs.append(item_clean)
        return list(asg_map.values()) if asg_map else existing_asgs

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
            student_doc = coll.find_one({
                "$or": [
                    {"RegNumber": reg_no},
                    {"RegNumber": {"$regex": f"^{reg_clean}$", "$options": "i"}},
                ]
            })

            if not student_doc:
                print(f"[Database] Notice: No student found with RegNumber '{reg_no}' in Cluster0 — assignment(s) not saved.")
                continue

            existing_asgs = student_doc.get("Assignments") or []
            updated_asg_list = _merge(existing_asgs, new_items, reg_no)

            coll.update_one(
                {"_id": student_doc["_id"]},
                {"$set": {"Assignments": updated_asg_list}}
            )
            print(f"[Database] Saved {len(updated_asg_list)} assignment(s) on {reg_no}'s document in Atlas ({DB_NAME}.{COLLECTION_NAME})")
            saved_ok = True
        client.close()
    except Exception as exc:
        print(f"[Database] Notice: Could not save assignments to Cluster0 Atlas ({exc.__class__.__name__}).")

    # Sync to student's Scores dictionary too, so topic-score views stay consistent
    for item in assignments_list:
        reg = item.get("studentRegisterNumber") or item.get("regNo")
        tr = item.get("track", "AI")
        top = item.get("topic")
        sc = item.get("score")
        if reg and top:
            sync_assignment_to_student_record(reg, tr, top, sc)

    return saved_ok