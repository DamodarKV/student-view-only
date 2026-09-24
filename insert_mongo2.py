"""
insert_mongo.py
---------------
MongoDB Atlas connection, student data CRUD, and one-time/full-replacement
seed utility.

MongoDB Atlas is the sole data store for reads/writes at runtime.

The local JSON file is used ONLY as a seed source when
replace_students_from_json() is explicitly executed.

IMPORTANT:
    replace_students_from_json() performs a FULL REPLACEMENT.

    Example:
        Existing MongoDB students = 177
        New JSON students        = 177

        Old 177 are deleted.
        New 177 are inserted.

        Final MongoDB count = 177

    The JSON data is NEVER appended to the existing collection.
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

USERNAME = ""
PASSWORD = ""
CLUSTER_HOST = ""

DB_NAME = "Cluster0"
COLLECTION_NAME = "students"

BASE_DIR = Path(__file__).resolve().parent

# ------------------------------------------------------------
# IMPORTANT:
# This JSON file is ONLY used as a seed source.
# Runtime application reads/writes MongoDB Atlas.
# ------------------------------------------------------------
JSON_FILE_PATH = BASE_DIR / "students_output_accuracy.json"

INSTRUCTOR_COLLECTION_NAME = "insturctor"


# ============================================================
# MONGODB URI
# ============================================================

def get_mongo_uri() -> str:
    """
    Builds the MongoDB Atlas connection URI.
    """

    user = quote_plus(USERNAME)
    pwd = quote_plus(PASSWORD)

    return (
        f"mongodb+srv://{user}:{pwd}@{CLUSTER_HOST}/"
        f"?retryWrites=true&w=majority&appName=Cluster0"
    )


# ============================================================
# CONNECTION POOLING
# ============================================================
#
# A single MongoClient is reused by the application.
#
# MongoClient:
#   - is thread-safe
#   - maintains an internal connection pool
#   - avoids creating a new TLS connection for every request
#
# The client is created lazily the first time it is needed.
# ============================================================

_client: Optional[MongoClient] = None

_client_lock = threading.Lock()

_indexes_ensured = False


def get_mongo_client() -> MongoClient:
    """
    Returns the shared MongoDB client.

    The client is created only once per process.
    """

    global _client

    # Fast path:
    # Reuse existing client.
    if _client is not None:
        return _client

    # Thread-safe creation.
    with _client_lock:

        if _client is None:

            kwargs: Dict[str, Any] = {
                "server_api": ServerApi("1"),

                "serverSelectionTimeoutMS": 5000,
                "connectTimeoutMS": 5000,
                "socketTimeoutMS": 10000,

                # Connection pool.
                "minPoolSize": 3,
                "maxPoolSize": 50,
                "maxIdleTimeMS": 60000,

                "retryWrites": True,
            }

            if certifi:
                kwargs["tlsCAFile"] = certifi.where()

            _client = MongoClient(
                get_mongo_uri(),
                **kwargs
            )

            # Ensure required indexes.
            _ensure_indexes(_client)

        return _client


def close_mongo_client() -> None:
    """
    Call this ONLY when the application itself shuts down.

    Do NOT call this after every database operation.
    """

    global _client

    with _client_lock:

        if _client is not None:

            _client.close()

            _client = None


# ============================================================
# INDEX SETUP
# ============================================================

def _ensure_indexes(client: MongoClient) -> None:
    """
    Creates the RegNumber index once.

    This improves student lookup performance.
    """

    global _indexes_ensured

    if _indexes_ensured:
        return

    try:

        coll = client[DB_NAME][COLLECTION_NAME]

        coll.create_index(
            [("RegNumber", ASCENDING)],
            name="regnumber_idx",
            background=True
        )

        _indexes_ensured = True

        print("[MongoDB Atlas] RegNumber index ensured.")

    except Exception as exc:

        print(
            "[Database] Notice: Could not ensure indexes "
            f"({exc.__class__.__name__})."
        )


# ============================================================
# FETCH ALL STUDENTS
# ============================================================

def fetch_students_data() -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetches all student data from MongoDB Atlas.

    No local JSON fallback is used.
    """

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        docs = list(
            coll.find(
                {},
                {"_id": 0}
            )
        )

        if docs:

            print(
                f"[MongoDB Atlas] Retrieved {len(docs)} student "
                f"records from {DB_NAME}.{COLLECTION_NAME}"
            )

            return docs, "MongoDB Atlas"

    except Exception as exc:

        print(
            "[MongoDB Atlas] Notice: Atlas connection not available "
            f"({exc.__class__.__name__})."
        )

    return [], "None"


# ============================================================
# FETCH SINGLE STUDENT
# ============================================================

def fetch_single_student(
    register_number: str
) -> Optional[Dict[str, Any]]:
    """
    Fetches one student by RegNumber.
    """

    reg_clean = register_number.strip()

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber": reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex": f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            },
            {"_id": 0}
        )

        return doc

    except Exception as exc:

        print(
            f"[Database] Notice: Could not fetch student "
            f"{register_number} ({exc.__class__.__name__})."
        )

        return None


# ============================================================
# WATCHLIST
# ============================================================

def update_student_watchlist(
    reg_number: str,
    on_watchlist: bool
) -> bool:
    """
    Updates WatchListStatus for a student.

    TrackStatus is NOT modified.
    """

    status_val = "Watchlist" if on_watchlist else ""

    reg_clean = reg_number.strip()

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        res = coll.update_one(
            {
                "$or": [
                    {
                        "RegNumber": reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex": f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            },
            {
                "$set": {
                    "WatchListStatus": status_val
                }
            }
        )

        if res.matched_count > 0:

            print(
                f"[MongoDB Atlas] Updated "
                f"WatchListStatus='{status_val}' "
                f"for {reg_number}"
            )

            return True

    except Exception as exc:

        print(
            "[MongoDB Atlas] Notice: Atlas update skipped "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# UPDATE TRACK SCORES
# ============================================================

def update_student_track_scores(
    reg_number: str,
    ai_score: Optional[float],
    devops_score: Optional[float],
    aggregate_score: Optional[float]
) -> bool:
    """
    Updates AverageScore for AI, DevOps and Aggregate.

    Scores topic dictionary remains untouched.
    """

    reg_clean = reg_number.strip()

    update_fields: Dict[str, Any] = {}

    if ai_score is not None:
        update_fields["AverageScore.AI"] = round(
            float(ai_score),
            2
        )

    if devops_score is not None:
        update_fields["AverageScore.DevOps"] = round(
            float(devops_score),
            2
        )

    if aggregate_score is not None:
        update_fields["AverageScore.Aggregate"] = round(
            float(aggregate_score),
            2
        )

    if not update_fields:
        return False

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        res = coll.update_one(
            {
                "$or": [
                    {
                        "RegNumber": reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex": f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            },
            {
                "$set": update_fields
            }
        )

        if res.matched_count > 0:

            print(
                f"[MongoDB Atlas] Updated track scores "
                f"for {reg_number}: {update_fields}"
            )

            return True

    except Exception as exc:

        print(
            "[MongoDB Atlas] Notice: Atlas update track scores "
            f"skipped ({exc.__class__.__name__})."
        )

    return False


# ============================================================
# ADD / UPDATE TOPIC SCORE
# ============================================================

def add_student_topic_score(
    reg_number: str,
    track: str,
    topic_name: str,
    score: float
) -> bool:
    """
    Adds or updates a topic score.

    Recalculates:
        - AI average
        - DevOps average
        - Aggregate average
    """

    reg_clean = reg_number.strip()

    track_key = (
        "DevOps"
        if "devops" in track.lower()
        else "AI"
    )

    topic_clean = topic_name.strip()

    score_val = round(float(score), 2)

    def _recalc_doc_scores(doc: dict):

        if (
            "Scores" not in doc
            or not isinstance(doc["Scores"], dict)
        ):
            doc["Scores"] = {
                "AI": {},
                "DevOps": {}
            }

        if (
            track_key not in doc["Scores"]
            or not isinstance(
                doc["Scores"][track_key],
                dict
            )
        ):
            doc["Scores"][track_key] = {}

        doc["Scores"][track_key][topic_clean] = score_val

        if (
            "AverageScore" not in doc
            or not isinstance(
                doc["AverageScore"],
                dict
            )
        ):
            doc["AverageScore"] = {}

        has_devops = (
            track_key == "DevOps"
            or (
                doc.get("AverageScore", {}).get("DevOps")
                is not None
                and str(
                    doc.get("AverageScore", {}).get("DevOps")
                ).strip() != ""
            )
            or bool(
                doc.get("Scores", {}).get("DevOps")
            )
        )

        calc_res = (
            average.calculate_student_averages_from_topics(
                doc["Scores"],
                has_devops=has_devops
            )
        )

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

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber": reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex": f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            }
        )

        if doc:

            ai_avg, devops_avg, agg = _recalc_doc_scores(doc)

            set_dict = {
                f"Scores.{track_key}.{topic_clean}": score_val,

                "AverageScore.AI": ai_avg,

                "AverageScore.Aggregate": agg,
            }

            if devops_avg is not None:

                set_dict[
                    "AverageScore.DevOps"
                ] = devops_avg

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$set": set_dict,

                    "$pull": {
                        f"AssignmentTopics.{track_key}":
                            topic_clean
                    }
                }
            )

            print(
                f"[Database] Updated topic "
                f"'{topic_clean}'={score_val} "
                f"for {reg_number}"
            )

            return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not update "
            "MongoDB Atlas topic score "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# ADD EXAM TO ALL STUDENTS
# ============================================================

def add_exam_to_all_students(
    topic_name: str,
    track_name: str
) -> Tuple[bool, str, int]:
    """
    Creates a new topic/exam for ALL students.

    New topic is initialized to None.

    AverageScore is recalculated for all students.
    """

    clean_topic = topic_name.strip()

    if not clean_topic:

        return (
            False,
            "Topic name cannot be empty",
            0
        )

    track_key = (
        "DevOps"
        if "devops" in track_name.lower()
        else "AI"
    )

    topic_key = average.to_topic_key(clean_topic)

    if not topic_key:

        return (
            False,
            "Invalid topic name",
            0
        )

    existing_topics = (
        average.get_devops_topics()
        if track_key == "DevOps"
        else average.get_ai_topics()
    )

    norm_new = average.normalize_topic_name(
        topic_key
    )

    for t in existing_topics:

        if (
            average.normalize_topic_name(t)
            == norm_new
        ):

            return (
                False,
                f"Topic already exists in {track_key} Track.",
                0
            )

    try:

        client = get_mongo_client()

        db_instance = client[DB_NAME]

        coll = db_instance[COLLECTION_NAME]

        # ----------------------------------------------------
        # Add topic with None score to every student.
        # ----------------------------------------------------

        res = coll.update_many(
            {},
            {
                "$set": {
                    f"Scores.{track_key}.{topic_key}":
                        None
                }
            }
        )

        updated_count = res.matched_count

        # ----------------------------------------------------
        # Register topic.
        # ----------------------------------------------------

        average.register_topic(
            track_key,
            topic_key
        )

        # ----------------------------------------------------
        # Recalculate averages.
        # ----------------------------------------------------

        for doc in coll.find(
            {},
            {
                "Scores": 1,
                "AverageScore": 1
            }
        ):

            has_devops = (
                track_key == "DevOps"
                or (
                    doc.get(
                        "AverageScore",
                        {}
                    ).get("DevOps")
                    is not None
                    and str(
                        doc.get(
                            "AverageScore",
                            {}
                        ).get("DevOps")
                    ).strip() != ""
                )
                or bool(
                    doc.get(
                        "Scores",
                        {}
                    ).get("DevOps")
                )
            )

            calc_res = (
                average.calculate_student_averages_from_topics(
                    doc.get("Scores", {}),
                    has_devops=has_devops
                )
            )

            set_dict = {
                "AverageScore.AI":
                    calc_res["AI"],

                "AverageScore.Aggregate":
                    calc_res["Aggregate"],
            }

            if calc_res["DevOps"] is not None:

                set_dict[
                    "AverageScore.DevOps"
                ] = calc_res["DevOps"]

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$set": set_dict
                }
            )

        # ----------------------------------------------------
        # Add topic to instructor syllabus.
        # ----------------------------------------------------

        try:

            add_instructor_topic(
                topic_name=clean_topic,
                track_name=f"{track_key} Track",
                status="upcoming",
                date=None
            )

        except Exception as e:

            print(
                "[MongoDB Atlas] Notice: Failed to append "
                f"to instructor syllabus: {e}"
            )

        print(
            f"[MongoDB Atlas] Added exam '{topic_key}' "
            f"to all {updated_count} students "
            f"in {track_key} Track"
        )

        return (
            True,
            topic_key,
            updated_count
        )

    except Exception as exc:

        print(
            f"[MongoDB Atlas] Error adding exam "
            f"to all students: {exc}"
        )

        return (
            False,
            str(exc),
            0
        )


# ============================================================
# DELETE TOPIC SCORE
# ============================================================

def delete_student_topic_score(
    reg_number: str,
    track: str,
    topic_name: str
) -> bool:
    """
    Deletes a topic score and recalculates averages.
    """

    reg_clean = reg_number.strip()

    track_key = (
        "DevOps"
        if "devops" in track.lower()
        else "AI"
    )

    topic_clean = topic_name.strip()

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber": reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex": f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            }
        )

        if not doc:
            return False

        target_key = None

        if (
            "Scores" in doc
            and isinstance(doc["Scores"], dict)
            and track_key in doc["Scores"]
        ):

            track_dict = doc["Scores"][track_key]

            if isinstance(track_dict, dict):

                norm_target = (
                    average.normalize_topic_name(
                        topic_clean
                    )
                )

                for k in list(track_dict.keys()):

                    if (
                        k == topic_clean
                        or k.strip().lower()
                        == topic_clean.lower()
                        or average.normalize_topic_name(k)
                        == norm_target
                    ):

                        target_key = k

                        del track_dict[k]

                        break

        if not target_key:

            target_key = average.to_topic_key(
                topic_clean
            )

            if (
                "Scores" in doc
                and isinstance(doc["Scores"], dict)
                and track_key in doc["Scores"]
            ):

                if (
                    target_key
                    in doc["Scores"][track_key]
                ):

                    del doc["Scores"][track_key][target_key]

        if (
            "AverageScore" not in doc
            or not isinstance(
                doc["AverageScore"],
                dict
            )
        ):

            doc["AverageScore"] = {}

        has_devops = (
            track_key == "DevOps"
            or (
                doc.get(
                    "AverageScore",
                    {}
                ).get("DevOps")
                is not None
                and str(
                    doc.get(
                        "AverageScore",
                        {}
                    ).get("DevOps")
                ).strip() != ""
            )
            or bool(
                doc.get(
                    "Scores",
                    {}
                ).get("DevOps")
            )
        )

        calc_res = (
            average.calculate_student_averages_from_topics(
                doc.get("Scores", {}),
                has_devops=has_devops
            )
        )

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

            set_dict[
                "AverageScore.DevOps"
            ] = devops_avg

        unset_key = target_key or topic_clean

        coll.update_one(
            {
                "_id": doc["_id"]
            },
            {
                "$unset": {
                    f"Scores.{track_key}.{unset_key}":
                        ""
                },

                "$set": set_dict,
            }
        )

        print(
            f"[Database] Deleted topic "
            f"'{unset_key}' for {reg_number}"
        )

        return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not delete "
            "Atlas topic score "
            f"({exc.__class__.__name__}): {exc}"
        )

    return False


# ============================================================
# ADD STUDENT WEIGHT
# ============================================================

def add_student_weight(
    reg_number: str,
    assessment_type: str,
    weightage: float
) -> bool:
    """
    Adds or updates assessment weightage.
    """

    reg_clean = reg_number.strip()

    type_clean = assessment_type.strip()

    weight_val = round(
        float(weightage),
        2
    )

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber": reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex": f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            }
        )

        if doc:

            weights = doc.get("Weightage")

            if not isinstance(weights, list):
                weights = []

            updated = False

            for w in weights:

                if (
                    isinstance(w, dict)
                    and (
                        w.get("AssessmentType")
                        or ""
                    ).strip().lower()
                    == type_clean.lower()
                ):

                    w["AssessmentType"] = type_clean

                    w["Weightage"] = weight_val

                    updated = True

                    break

            if not updated:

                weights.append(
                    {
                        "AssessmentType":
                            type_clean,

                        "Weightage":
                            weight_val
                    }
                )

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$set": {
                        "Weightage": weights
                    }
                }
            )

            print(
                f"[Database] Updated weightage "
                f"'{type_clean}'={weight_val} "
                f"for {reg_number}"
            )

            return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not update "
            "MongoDB Atlas weightage "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# DELETE STUDENT WEIGHT
# ============================================================

def delete_student_weight(
    reg_number: str,
    assessment_type: str
) -> bool:
    """
    Deletes an assessment weightage entry.
    """

    reg_clean = reg_number.strip()

    type_clean = assessment_type.strip()

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber": reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex": f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            }
        )

        if doc:

            weights = doc.get("Weightage")

            if not isinstance(weights, list):
                weights = []

            weights = [
                w
                for w in weights
                if not (
                    isinstance(w, dict)
                    and (
                        w.get("AssessmentType")
                        or ""
                    ).strip().lower()
                    == type_clean.lower()
                )
            ]

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$set": {
                        "Weightage": weights
                    }
                }
            )

            print(
                f"[Database] Deleted weightage "
                f"'{type_clean}' for {reg_number}"
            )

            return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not delete "
            "MongoDB Atlas weightage "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# MENTOR FEEDBACK
# ============================================================

def update_student_feedback(
    reg_number: str,
    note: str,
    feedback_from: Optional[str] = None,
    date: Optional[str] = None
) -> bool:
    """
    Updates mentor feedback.
    """

    reg_clean = reg_number.strip()

    note_clean = note.strip()

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber": reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex": f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            }
        )

        if doc:

            existing = doc.get(
                "MentorFeedback"
            )

            if not isinstance(existing, dict):
                existing = {}

            updated = {
                "Note": note_clean,

                "From": (
                    feedback_from.strip()
                    if feedback_from
                    else existing.get("From", "")
                ),

                "Date": (
                    date.strip()
                    if date
                    else existing.get("Date", "")
                ),
            }

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$set": {
                        "MentorFeedback":
                            updated
                    }
                }
            )

            print(
                f"[Database] Updated mentor feedback "
                f"for {reg_number}"
            )

            return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not update "
            "MongoDB Atlas mentor feedback "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# UPDATE STUDENT TOPIC SCORE
# ============================================================

def update_student_topic_score(
    reg_number: str,
    old_track: str,
    old_topic: str,
    new_track: str,
    new_topic: str,
    new_score: float
) -> bool:
    """
    Updates an existing topic score.

    Supports:
        - score change
        - topic rename
        - track change
    """

    old_track_key = (
        "DevOps"
        if "devops" in old_track.lower()
        else "AI"
    )

    new_track_key = (
        "DevOps"
        if "devops" in new_track.lower()
        else "AI"
    )

    if (
        old_track_key != new_track_key
        or old_topic.strip().lower()
        != new_topic.strip().lower()
    ):

        delete_student_topic_score(
            reg_number,
            old_track,
            old_topic
        )

    return add_student_topic_score(
        reg_number,
        new_track,
        new_topic,
        new_score
    )


# ============================================================
# FETCH INSTRUCTOR DATA
# ============================================================

def fetch_instructor_data() -> Tuple[Dict[str, Any], str]:
    """
    Fetches instructor data from MongoDB Atlas.

    Supports both:
        insturctor
        instructor
    """

    try:

        client = get_mongo_client()

        db = client[DB_NAME]

        for cname in [
            INSTRUCTOR_COLLECTION_NAME,
            "instructor"
        ]:

            if cname in db.list_collection_names():

                doc = db[cname].find_one(
                    {},
                    {"_id": 0}
                )

                if (
                    doc
                    and (
                        "ai_track" in doc
                        or "devops_track" in doc
                    )
                ):

                    print(
                        f"[MongoDB Atlas] Retrieved "
                        f"instructor data from "
                        f"{DB_NAME}.{cname}"
                    )

                    return doc, "MongoDB Atlas"

    except Exception as exc:

        print(
            "[MongoDB Atlas] Notice: Atlas instructor "
            "fetch skipped "
            f"({exc.__class__.__name__})."
        )

    return {}, "None"


# ============================================================
# TRACK NAME -> MONGODB KEY
# ============================================================

def _track_to_key(track_name: str) -> str:

    t = track_name.strip().lower()

    if "devops" in t:
        return "devops_track"

    return "ai_track"


# ============================================================
# UPDATE INSTRUCTOR TOPIC STATUS
# ============================================================

def update_instructor_topic_status(
    topic_name: str,
    track_name: str,
    new_status: str,
    completed_date: Optional[str] = None
) -> bool:
    """
    Updates topic status in instructor collection.
    """

    track_key = _track_to_key(track_name)

    status_lower = (
        new_status
        .strip()
        .lower()
        .replace(" ", "_")
    )

    topic_clean = topic_name.strip().lower()

    try:

        client = get_mongo_client()

        db = client[DB_NAME]

        updated = False

        for cname in [
            INSTRUCTOR_COLLECTION_NAME,
            "instructor"
        ]:

            if cname in db.list_collection_names():

                doc = db[cname].find_one({})

                if doc and track_key in doc:

                    topics = doc[
                        track_key
                    ].get(
                        "topics",
                        []
                    )

                    matched = False

                    for t in topics:

                        if (
                            t.get("topic", "")
                            .strip()
                            .lower()
                            == topic_clean
                        ):

                            t["status"] = status_lower

                            if completed_date is not None:

                                t["date_completed"] = (
                                    completed_date
                                )

                            matched = True

                            break

                    if matched:

                        db[cname].update_one(
                            {
                                "_id":
                                    doc["_id"]
                            },
                            {
                                "$set": {
                                    f"{track_key}.topics":
                                        topics
                                }
                            }
                        )

                        print(
                            f"[MongoDB Atlas] Updated "
                            f"topic '{topic_name}' "
                            f"status to '{new_status}' "
                            f"in {cname}"
                        )

                        updated = True

        return updated

    except Exception as exc:

        print(
            "[MongoDB Atlas] Notice: Could not update "
            "MongoDB topic status directly "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# ADD INSTRUCTOR TOPIC
# ============================================================

def add_instructor_topic(
    topic_name: str,
    track_name: str,
    status: str,
    date: Optional[str] = None
) -> bool:
    """
    Appends a new topic to instructor document.
    """

    track_key = _track_to_key(track_name)

    status_lower = (
        status
        .strip()
        .lower()
        .replace(" ", "_")
    )

    new_entry = {
        "topic": topic_name.strip(),
        "status": status_lower,
        "date_completed": date or ""
    }

    try:

        client = get_mongo_client()

        db = client[DB_NAME]

        updated = False

        for cname in [
            INSTRUCTOR_COLLECTION_NAME,
            "instructor"
        ]:

            if cname in db.list_collection_names():

                db[cname].update_one(
                    {},
                    {
                        "$push": {
                            f"{track_key}.topics":
                                new_entry
                        }
                    }
                )

                print(
                    f"[MongoDB Atlas] Added new topic "
                    f"'{topic_name}' to {cname}"
                )

                updated = True

        return updated

    except Exception as exc:

        print(
            "[MongoDB Atlas] Notice: Could not append "
            "topic to Atlas "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# FETCH STUDENT ASSIGNMENTS
# ============================================================

def fetch_student_assignments_from_mongo(
    register_number: str
) -> List[Dict[str, Any]]:
    """
    Fetches assignments from the student's Assignment field.
    """

    reg_clean = register_number.strip().lower()

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber":
                            register_number.strip()
                    },
                    {
                        "RegNumber": {
                            "$regex":
                                f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            },
            {
                "_id": 0,
                "Assignment": 1
            }
        )

        if (
            doc
            and isinstance(
                doc.get("Assignment"),
                list
            )
        ):

            print(
                f"[Database] Retrieved "
                f"{len(doc['Assignment'])} assignments "
                f"for {register_number}"
            )

            return doc["Assignment"]

    except Exception as exc:

        print(
            "[Database] Notice: Atlas assignment "
            "fetch skipped "
            f"({exc.__class__.__name__})."
        )

    return []


# ============================================================
# SYNC ASSIGNMENT TO STUDENT
# ============================================================

def sync_assignment_to_student_record(
    reg_number: str,
    track: str,
    topic_name: str,
    score: Optional[float] = None
) -> bool:
    """
    Syncs assignment into:

        Scores.[AI|DevOps]

    and marks it under:

        AssignmentTopics.[AI|DevOps]

    Assignment values remain distinguishable from genuine
    assessment scores.
    """

    reg_clean = reg_number.strip()

    track_key = (
        "DevOps"
        if "devops" in track.lower()
        else "AI"
    )

    topic_clean = topic_name.strip()

    score_val = (
        round(float(score), 2)
        if score is not None
        and score != ""
        else None
    )

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber":
                            reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex":
                                f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            }
        )

        if doc:

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$set": {
                        f"Scores.{track_key}.{topic_clean}":
                            score_val
                    },

                    "$addToSet": {
                        f"AssignmentTopics.{track_key}":
                            topic_clean
                    }
                }
            )

            print(
                f"[Database] Synced assignment "
                f"'{topic_clean}' "
                f"(Score: {score_val}) "
                f"to student {reg_number}"
            )

            return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not sync "
            "assignment to Atlas "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# UPDATE ASSIGNMENT
# ============================================================

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
    Updates an existing assignment.

    Does NOT convert assignment into a genuine assessment.
    """

    reg_clean = reg_number.strip()

    old_track_key = (
        "DevOps"
        if "devops" in old_track.lower()
        else "AI"
    )

    new_track_key = (
        "DevOps"
        if "devops" in new_track.lower()
        else "AI"
    )

    old_topic_clean = old_topic.strip()
    new_topic_clean = new_topic.strip()

    matched = False

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber":
                            reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex":
                                f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            }
        )

        if not doc:
            return False

        assignments = doc.get("Assignment") or []

        for a in assignments:

            same_id = (
                assignment_id
                and a.get("assignmentId")
                == assignment_id
            )

            same_topic = (
                not assignment_id
                and (
                    a.get("topic") or ""
                ).strip().lower()
                == old_topic_clean.lower()
                and (
                    a.get("track")
                    or "AI"
                )
                == old_track_key
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

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$set": {
                        "Assignment":
                            assignments
                    }
                }
            )

        # ----------------------------------------------------
        # Remove old Scores / AssignmentTopics entry if
        # topic or track changed.
        # ----------------------------------------------------

        if (
            old_track_key != new_track_key
            or old_topic_clean.lower()
            != new_topic_clean.lower()
        ):

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$unset": {
                        f"Scores.{old_track_key}.{old_topic_clean}":
                            ""
                    },

                    "$pull": {
                        f"AssignmentTopics.{old_track_key}":
                            old_topic_clean
                    }
                }
            )

        print(
            f"[Database] Updated assignment "
            f"'{new_topic_clean}' for {reg_number}"
        )

    except Exception as exc:

        print(
            "[Database] Notice: Could not update "
            "assignment in Atlas "
            f"({exc.__class__.__name__})."
        )

        matched = False

    # Re-sync new topic.
    sync_assignment_to_student_record(
        reg_number,
        new_track_key,
        new_topic_clean,
        new_score
    )

    return matched


# ============================================================
# DELETE ASSIGNMENT
# ============================================================

def delete_assignment_in_mongo(
    reg_number: str,
    assignment_id: Optional[str],
    track: str,
    topic: str
) -> bool:
    """
    Deletes assignment and corresponding Scores /
    AssignmentTopics data.
    """

    reg_clean = reg_number.strip()

    track_key = (
        "DevOps"
        if "devops" in track.lower()
        else "AI"
    )

    topic_clean = topic.strip()

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        doc = coll.find_one(
            {
                "$or": [
                    {
                        "RegNumber":
                            reg_clean
                    },
                    {
                        "RegNumber": {
                            "$regex":
                                f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            }
        )

        if not doc:
            return False

        assignments = doc.get(
            "Assignment"
        ) or []

        remaining = [
            a
            for a in assignments
            if not (
                (
                    assignment_id
                    and a.get("assignmentId")
                    == assignment_id
                )
                or (
                    not assignment_id
                    and (
                        a.get("topic") or ""
                    ).strip().lower()
                    == topic_clean.lower()
                    and (
                        a.get("track")
                        or "AI"
                    )
                    == track_key
                )
            )
        ]

        coll.update_one(
            {
                "_id": doc["_id"]
            },
            {
                "$set": {
                    "Assignment":
                        remaining
                },

                "$unset": {
                    f"Scores.{track_key}.{topic_clean}":
                        ""
                },

                "$pull": {
                    f"AssignmentTopics.{track_key}":
                        topic_clean
                }
            }
        )

        # Remove from assessment collections if present.
        for ass_coll_name in [
            "assesement",
            "assessment"
        ]:

            db[ass_coll_name].delete_many(
                {
                    "RegNumber":
                        reg_clean,

                    "Track":
                        track_key,

                    "Topic":
                        topic_clean
                }
            )

        print(
            f"[Database] Deleted assignment "
            f"'{topic_clean}' for {reg_number}"
        )

        return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not delete "
            "assignment in Atlas "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# SAVE ASSIGNMENTS
# ============================================================

def save_assignments_to_mongo(
    assignments_list: List[Dict[str, Any]]
) -> bool:
    """
    Saves assignments inside each student's Assignment field.

    No separate assignments collection is used.
    """

    if not assignments_list:
        return True

    def _clean_item(
        item: dict,
        student_reg: str
    ) -> dict:

        item_copy = dict(item)

        item_copy.pop(
            "_id",
            None
        )

        if "studentRegisterNumber" not in item_copy:

            item_copy[
                "studentRegisterNumber"
            ] = student_reg

        return item_copy

    grouped: Dict[
        str,
        List[Dict[str, Any]]
    ] = {}

    for item in assignments_list:

        reg = (
            item.get("studentRegisterNumber")
            or item.get("regNo")
            or ""
        )

        reg_clean = reg.strip()

        if reg_clean:

            grouped.setdefault(
                reg_clean,
                []
            ).append(item)

    saved_ok = False

    try:

        client = get_mongo_client()

        db = client[DB_NAME]
        coll = db[COLLECTION_NAME]

        for reg_no, new_items in grouped.items():

            reg_clean = reg_no.lower()

            query = {
                "$or": [
                    {
                        "RegNumber":
                            reg_no
                    },
                    {
                        "RegNumber": {
                            "$regex":
                                f"^{reg_clean}$",
                            "$options": "i"
                        }
                    }
                ]
            }

            student_doc = coll.find_one(query)

            existing_asgs = []

            if (
                student_doc
                and isinstance(
                    student_doc.get(
                        "Assignment"
                    ),
                    list
                )
            ):

                existing_asgs = (
                    student_doc["Assignment"]
                )

            asg_map = {
                a.get("assignmentId"):
                    a
                for a in existing_asgs
                if a.get("assignmentId")
            }

            for item in new_items:

                asg_id = item.get(
                    "assignmentId"
                )

                item_clean = _clean_item(
                    item,
                    reg_no
                )

                if asg_id:

                    asg_map[
                        asg_id
                    ] = item_clean

                else:

                    existing_asgs.append(
                        item_clean
                    )

            updated_asg_list = (
                list(asg_map.values())
                if asg_map
                else existing_asgs
            )

            if student_doc:

                coll.update_one(
                    {
                        "_id":
                            student_doc["_id"]
                    },
                    {
                        "$set": {
                            "Assignment":
                                updated_asg_list
                        }
                    }
                )

                print(
                    f"[Database] Saved "
                    f"{len(updated_asg_list)} assignment(s) "
                    f"for {reg_no}"
                )

            else:

                # Creates a minimal student document
                # if the student doesn't exist.
                coll.update_one(
                    query,
                    {
                        "$set": {
                            "RegNumber":
                                reg_no,

                            "Assignment":
                                updated_asg_list
                        }
                    },
                    upsert=True
                )

                print(
                    f"[Database] Student {reg_no} not found; "
                    f"created a new student record."
                )

            saved_ok = True

    except Exception as exc:

        print(
            "[Database] Notice: Could not save "
            "assignments to Students collection "
            f"({exc.__class__.__name__})."
        )

    # Sync assignments to Scores.
    for item in assignments_list:

        reg = (
            item.get(
                "studentRegisterNumber"
            )
            or item.get("regNo")
        )

        tr = item.get(
            "track",
            "AI"
        )

        top = item.get("topic")

        sc = item.get("score")

        if reg and top:

            sync_assignment_to_student_record(
                reg,
                tr,
                top,
                sc
            )

    return saved_ok


# ============================================================
# FULL JSON -> MONGODB REPLACEMENT
# ============================================================
#
# THIS IS THE IMPORTANT PART.
#
# This function does NOT append data.
#
# It performs:
#
#     JSON
#       ↓
#     Validate
#       ↓
#     Delete ALL old student documents
#       ↓
#     Insert NEW JSON documents
#       ↓
#     Verify count
#
# Example:
#
#     MongoDB = 177
#     JSON    = 177
#
#     DELETE 177
#     INSERT 177
#     FINAL = 177
#
# ============================================================

def replace_students_from_json() -> Dict[str, Any]:
    """
    Completely replaces the MongoDB students collection with
    the contents of the local JSON seed file.

    The JSON is READ ONLY.

    Nothing is written back to the JSON file.
    """

    # --------------------------------------------------------
    # STEP 1: Make sure JSON exists.
    # --------------------------------------------------------

    if not JSON_FILE_PATH.exists():

        raise FileNotFoundError(
            f"Seed JSON not found at: "
            f"{JSON_FILE_PATH}"
        )

    # --------------------------------------------------------
    # STEP 2: Load JSON.
    # --------------------------------------------------------

    print(
        f"[Seed] Reading seed JSON from:\n"
        f"{JSON_FILE_PATH}"
    )

    with open(
        JSON_FILE_PATH,
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    # --------------------------------------------------------
    # STEP 3: Normalize single object -> list.
    # --------------------------------------------------------

    if isinstance(data, dict):

        data = [data]

    # --------------------------------------------------------
    # STEP 4: Validate JSON structure.
    # --------------------------------------------------------

    if not isinstance(data, list):

        raise ValueError(
            "Seed JSON must contain a list "
            "of student records."
        )

    # --------------------------------------------------------
    # VERY IMPORTANT SAFETY CHECK
    #
    # Never delete existing MongoDB data if JSON is empty.
    # --------------------------------------------------------

    if not data:

        raise ValueError(
            "Seed JSON is empty. "
            "Existing MongoDB student data "
            "will NOT be deleted."
        )

    # --------------------------------------------------------
    # Validate every record.
    # --------------------------------------------------------

    for index, student in enumerate(data):

        if not isinstance(student, dict):

            raise ValueError(
                f"Invalid student record at index "
                f"{index}. Expected an object."
            )

    json_count = len(data)

    print(
        f"[Seed] Validated {json_count} student records."
    )

    # --------------------------------------------------------
    # STEP 5: Connect using shared MongoClient.
    # --------------------------------------------------------

    client = get_mongo_client()

    db = client[DB_NAME]

    coll = db[COLLECTION_NAME]

    # --------------------------------------------------------
    # STEP 6: Check existing count BEFORE replacement.
    # --------------------------------------------------------

    old_count = coll.count_documents({})

    print(
        f"[Seed] Existing MongoDB student count: "
        f"{old_count}"
    )

    # --------------------------------------------------------
    # STEP 7: DELETE ALL OLD STUDENT DOCUMENTS.
    #
    # IMPORTANT:
    #
    # We use delete_many({}) instead of drop().
    #
    # This removes documents but keeps indexes.
    # --------------------------------------------------------

    delete_result = coll.delete_many({})

    deleted_count = delete_result.deleted_count

    print(
        f"[Seed] Deleted {deleted_count} old "
        f"student documents."
    )

    # --------------------------------------------------------
    # STEP 8: INSERT THE NEW JSON DATA.
    # --------------------------------------------------------

    try:

        insert_result = coll.insert_many(
            data,
            ordered=True
        )

        inserted_count = len(
            insert_result.inserted_ids
        )

        print(
            f"[Seed] Inserted {inserted_count} "
            f"new student documents."
        )

    except Exception as exc:

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # At this point old documents have already been
        # deleted. If insertion fails, the collection may
        # be partially populated.
        #
        # We report the failure clearly instead of pretending
        # the seed succeeded.
        # ----------------------------------------------------

        print(
            "[Seed] ERROR: Failed while inserting "
            f"new student data: {exc}"
        )

        raise

    # --------------------------------------------------------
    # STEP 9: VERIFY FINAL COUNT.
    # --------------------------------------------------------

    final_count = coll.count_documents({})

    print(
        f"[Seed] Final MongoDB student count: "
        f"{final_count}"
    )

    # --------------------------------------------------------
    # STEP 10: Safety verification.
    # --------------------------------------------------------

    if final_count != json_count:

        raise RuntimeError(
            "Seed verification failed. "
            f"JSON contains {json_count} records, "
            f"but MongoDB contains {final_count}."
        )

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    return {
        "status": "success",

        "json_records":
            json_count,

        "old_mongodb_records":
            old_count,

        "deleted_old_records":
            deleted_count,

        "inserted_new_records":
            inserted_count,

        "final_mongodb_count":
            final_count,

        "message": (
            f"Successfully replaced "
            f"{deleted_count} old student records "
            f"with {inserted_count} new records. "
            f"Final MongoDB count: {final_count}"
        )
    }


# ============================================================
# BACKWARD-COMPATIBLE ALIAS
# ============================================================
#
# If some other part of your project already imports:
#
#     push_students_to_mongo
#
# it will continue working.
#
# Internally it now performs FULL REPLACEMENT.
# ============================================================

def push_students_to_mongo() -> Dict[str, Any]:
    """
    Backward-compatible wrapper.

    Performs FULL replacement of the students collection
    using the local JSON seed file.
    """

    return replace_students_from_json()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("MongoDB Atlas Student Data Seed Utility")
    print("=" * 60)

    print(
        f"\nSeed JSON:\n{JSON_FILE_PATH}"
    )

    try:

        result = replace_students_from_json()

        print("\n" + "=" * 60)
        print("MONGODB ATLAS SEED SUCCESSFUL")
        print("=" * 60)

        print(
            f"Database       : {DB_NAME}"
        )

        print(
            f"Collection     : {COLLECTION_NAME}"
        )

        print(
            f"JSON records   : "
            f"{result['json_records']}"
        )

        print(
            f"Old records    : "
            f"{result['old_mongodb_records']}"
        )

        print(
            f"Deleted        : "
            f"{result['deleted_old_records']}"
        )

        print(
            f"Inserted       : "
            f"{result['inserted_new_records']}"
        )

        print(
            f"Final count    : "
            f"{result['final_mongodb_count']}"
        )

        print(
            "\n" + result["message"]
        )

        print("=" * 60)

    except FileNotFoundError as exc:

        print(
            f"\n[!] Seed file error: {exc}"
        )

    except ValueError as exc:

        print(
            f"\n[!] Invalid seed data: {exc}"
        )

    except Exception as exc:

        print(
            "\n[!] MongoDB seed operation failed:"
        )

        print(
            f"{exc.__class__.__name__}: {exc}"
        )


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
