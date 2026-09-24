"""
db.py
-----
Handles the MongoDB Atlas connection and all data fetch/update logic.

MongoDB Atlas is the sole data store — there is no local JSON fallback.

Architecture:
    insert_mongo.py
        -> Responsible for importing/replacing student data from JSON.

    db.py
        -> Responsible for runtime application reads/writes/CRUD.

MongoClient is shared and pooled for the lifetime of the application.
"""

import logging
import re
import threading
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

USERNAME = ""
PASSWORD = ""
CLUSTER_HOST = ""

DB_NAME = "Cluster0"
COLLECTION_NAME = "students"
INSTRUCTOR_COLLECTION_NAME = "insturctor"


# ============================================================
# SHARED MONGODB CLIENT
# ============================================================

_client = None
_client_lock = threading.Lock()
_indexes_ensured = False


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


def get_mongo_client():
    """
    Returns one shared MongoClient.

    MongoClient is thread-safe and internally maintains a connection pool,
    so creating one client and reusing it is the recommended approach.
    """

    global _client

    if _client is not None:
        return _client

    with _client_lock:

        if _client is not None:
            return _client

        from pymongo import MongoClient
        from pymongo.server_api import ServerApi

        kwargs: Dict[str, Any] = {
            "server_api": ServerApi("1"),

            # Connection timeout settings
            "serverSelectionTimeoutMS": 5000,
            "connectTimeoutMS": 5000,
            "socketTimeoutMS": 10000,

            # Connection pool settings
            "minPoolSize": 3,
            "maxPoolSize": 50,
            "maxIdleTimeMS": 60000,

            # Retry temporary network failures
            "retryWrites": True,
        }

        if certifi:
            kwargs["tlsCAFile"] = certifi.where()

        _client = MongoClient(
            get_mongo_uri(),
            **kwargs
        )

        logger.info("MongoDB shared client initialized.")

    return _client


def close_mongo_client():
    """
    Closes the shared MongoDB client.

    Call this once when the application shuts down.

    Do NOT call this after every database operation.
    """

    global _client
    global _indexes_ensured

    with _client_lock:

        if _client is not None:

            try:
                _client.close()
                logger.info("MongoDB shared client closed.")
            except Exception as exc:
                logger.warning(
                    "Error while closing MongoDB client: %s",
                    exc,
                )

            _client = None
            _indexes_ensured = False


# ============================================================
# DATABASE HELPERS
# ============================================================

def _get_students_collection():
    """
    Returns the main students collection.
    """

    client = get_mongo_client()

    db = client[DB_NAME]

    return db[COLLECTION_NAME]


def _get_database():
    """
    Returns the configured MongoDB database.
    """

    client = get_mongo_client()

    return client[DB_NAME]


def _ensure_indexes():
    """
    Ensures useful indexes exist.

    RegNumber is indexed because almost all runtime operations
    search students using their registration number.

    The index is intentionally NOT unique so existing data is not
    rejected if duplicate RegNumber records already exist.
    """

    global _indexes_ensured

    if _indexes_ensured:
        return

    try:

        coll = _get_students_collection()

        coll.create_index(
            [("RegNumber", 1)],
            name="regnumber_index",
        )

        _indexes_ensured = True

        logger.info(
            "MongoDB indexes verified for %s.%s",
            DB_NAME,
            COLLECTION_NAME,
        )

    except Exception as exc:

        logger.warning(
            "Could not ensure MongoDB indexes: %s",
            exc,
        )


def _student_query(reg_number: str) -> Dict[str, Any]:
    """
    Creates a case-insensitive student lookup query.

    The registration number is escaped so special regex characters
    cannot accidentally change the query pattern.
    """

    reg_clean = reg_number.strip()

    escaped = re.escape(reg_clean)

    return {
        "$or": [
            {
                "RegNumber": reg_clean
            },
            {
                "RegNumber": {
                    "$regex": f"^{escaped}$",
                    "$options": "i",
                }
            },
        ]
    }


# ============================================================
# STUDENT DATA
# ============================================================

def fetch_students_data() -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetches all student documents from MongoDB Atlas.

    Returns:
        (students_list, source_name)

    On failure:
        ([], "None")
    """

    try:

        coll = _get_students_collection()

        _ensure_indexes()

        docs = list(
            coll.find(
                {},
                {"_id": 0}
            )
        )

        if docs:

            print(
                f"[Database] Successfully retrieved "
                f"{len(docs)} student records from MongoDB Atlas "
                f"({DB_NAME}.{COLLECTION_NAME})"
            )

            return docs, "MongoDB Atlas"

    except Exception as exc:

        print(
            f"[Database] Notice: MongoDB Atlas not reachable "
            f"({exc.__class__.__name__})."
        )

    return [], "None"


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

    Returns True if a matching student document was updated.
    """

    status_val = "Watchlist" if on_watchlist else ""

    try:

        coll = _get_students_collection()

        _ensure_indexes()

        res = coll.update_one(
            _student_query(reg_number),
            {
                "$set": {
                    "WatchListStatus": status_val
                }
            }
        )

        if res.matched_count > 0:

            print(
                f"[Database] Updated "
                f"WatchListStatus='{status_val}' "
                f"for {reg_number} in MongoDB Atlas"
            )

            return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not update "
            f"MongoDB Atlas directly "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# TRACK SCORES
# ============================================================

def update_student_track_scores(
    reg_number: str,
    ai_score: Optional[float],
    devops_score: Optional[float],
    aggregate_score: Optional[float],
) -> bool:
    """
    Updates AverageScore for AI, DevOps and Aggregate.

    Leaves the Scores topic dictionary intact.
    """

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

        coll = _get_students_collection()

        _ensure_indexes()

        res = coll.update_one(
            _student_query(reg_number),
            {
                "$set": update_fields
            }
        )

        if res.matched_count > 0:

            print(
                f"[Database] Updated track scores "
                f"for {reg_number}: {update_fields}"
            )

            return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not update track scores "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# ADD / UPDATE TOPIC SCORE
# ============================================================

def add_student_topic_score(
    reg_number: str,
    track: str,
    topic_name: str,
    score: float,
) -> bool:
    """
    Adds or updates a topic score under Scores.[AI|DevOps].

    Recalculates:
        - AI average
        - DevOps average
        - Aggregate average

    A genuine assessment score removes the topic from
    AssignmentTopics because it is no longer considered
    assignment-only data.
    """

    track_key = (
        "DevOps"
        if "devops" in track.lower()
        else "AI"
    )

    topic_clean = topic_name.strip()

    score_val = round(
        float(score),
        2
    )

    def _recalc_doc_scores(doc: dict):

        if (
            "Scores" not in doc
            or not isinstance(doc["Scores"], dict)
        ):
            doc["Scores"] = {
                "AI": {},
                "DevOps": {},
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
                doc["Scores"],
                has_devops=has_devops,
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

        coll = _get_students_collection()

        _ensure_indexes()

        doc = coll.find_one(
            _student_query(reg_number)
        )

        if not doc:
            return False

        ai_avg, devops_avg, agg = (
            _recalc_doc_scores(doc)
        )

        set_dict = {
            f"Scores.{track_key}.{topic_clean}": score_val,
            "AverageScore.AI": ai_avg,
            "AverageScore.Aggregate": agg,
        }

        if devops_avg is not None:
            set_dict["AverageScore.DevOps"] = devops_avg

        coll.update_one(
            {
                "_id": doc["_id"]
            },
            {
                "$set": set_dict,

                # This topic is now a genuine assessment.
                "$pull": {
                    f"AssignmentTopics.{track_key}":
                        topic_clean
                },
            }
        )

        print(
            f"[Database] Updated topic "
            f"'{topic_clean}' for {reg_number} "
            f"in MongoDB Atlas students collection"
        )

        return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not save "
            f"Atlas topic score "
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
    Creates a new topic/exam for the specified track across
    ALL student documents.

    Initializes the topic with None.

    Recalculates AverageScore for all students.

    Also appends the topic to the instructor syllabus.

    Returns:
        (success, message/topic_key, updated_count)
    """

    clean_topic = topic_name.strip()

    if not clean_topic:
        return (
            False,
            "Topic name cannot be empty",
            0,
        )

    track_key = (
        "DevOps"
        if "devops" in track_name.lower()
        else "AI"
    )

    topic_key = average.to_topic_key(
        clean_topic
    )

    if not topic_key:
        return (
            False,
            "Invalid topic name",
            0,
        )

    # --------------------------------------------------------
    # Duplicate check
    # --------------------------------------------------------

    existing_topics = (
        average.get_devops_topics()
        if track_key == "DevOps"
        else average.get_ai_topics()
    )

    norm_new = average.normalize_topic_name(
        topic_key
    )

    for topic in existing_topics:

        if (
            average.normalize_topic_name(topic)
            == norm_new
        ):
            return (
                False,
                f"Topic already exists in {track_key} Track.",
                0,
            )

    try:

        coll = _get_students_collection()

        _ensure_indexes()

        # ----------------------------------------------------
        # 1. Add topic to every student
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
        # 2. Register central topic
        # ----------------------------------------------------

        average.register_topic(
            track_key,
            topic_key
        )

        # ----------------------------------------------------
        # 3. Recalculate averages
        # ----------------------------------------------------

        for doc in coll.find(
            {},
            {
                "Scores": 1,
                "AverageScore": 1,
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
                    has_devops=has_devops,
                )
            )

            set_dict = {
                "AverageScore.AI":
                    calc_res["AI"],

                "AverageScore.Aggregate":
                    calc_res["Aggregate"],
            }

            if calc_res["DevOps"] is not None:

                set_dict["AverageScore.DevOps"] = (
                    calc_res["DevOps"]
                )

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$set": set_dict
                }
            )

        # ----------------------------------------------------
        # 4. Append to instructor syllabus
        # ----------------------------------------------------

        try:

            add_instructor_topic(
                topic_name=clean_topic,
                track_name=f"{track_key} Track",
                status="upcoming",
                date=None,
            )

        except Exception as exc:

            print(
                "[Database] Notice: Failed to append "
                f"to instructor syllabus: {exc}"
            )

        print(
            f"[Database] Added exam '{topic_key}' "
            f"to all {updated_count} students "
            f"in {track_key} Track"
        )

        return (
            True,
            topic_key,
            updated_count,
        )

    except Exception as exc:

        print(
            "[Database] Error adding exam "
            f"to all students: {exc}"
        )

        return (
            False,
            str(exc),
            0,
        )


# ============================================================
# DELETE TOPIC SCORE
# ============================================================

def delete_student_topic_score(
    reg_number: str,
    track: str,
    topic_name: str,
) -> bool:
    """
    Deletes a topic score under Scores.[AI|DevOps].

    Recalculates track average and aggregate score.
    """

    track_key = (
        "DevOps"
        if "devops" in track.lower()
        else "AI"
    )

    topic_clean = topic_name.strip()

    try:

        coll = _get_students_collection()

        _ensure_indexes()

        doc = coll.find_one(
            _student_query(reg_number)
        )

        if not doc:
            return False

        # ----------------------------------------------------
        # Locate exact topic key
        # ----------------------------------------------------

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

                for key in list(track_dict.keys()):

                    if (
                        key == topic_clean
                        or key.strip().lower()
                        == topic_clean.lower()
                        or average.normalize_topic_name(key)
                        == norm_target
                    ):

                        target_key = key

                        del track_dict[key]

                        break

        # ----------------------------------------------------
        # Fallback topic key conversion
        # ----------------------------------------------------

        if not target_key:

            target_key = average.to_topic_key(
                topic_clean
            )

            if (
                "Scores" in doc
                and isinstance(doc["Scores"], dict)
                and track_key in doc["Scores"]
            ):

                if target_key in doc["Scores"][track_key]:

                    del doc["Scores"][track_key][
                        target_key
                    ]

        # ----------------------------------------------------
        # Recalculate averages
        # ----------------------------------------------------

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
                has_devops=has_devops,
            )
        )

        ai_avg = calc_res["AI"]
        devops_avg = calc_res["DevOps"]
        agg = calc_res["Aggregate"]

        set_dict = {
            "AverageScore.AI": ai_avg,
            "AverageScore.Aggregate": agg,
        }

        if devops_avg is not None:
            set_dict["AverageScore.DevOps"] = (
                devops_avg
            )

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
            f"'{unset_key}' for {reg_number} "
            f"in MongoDB Atlas students collection"
        )

        return True

    except Exception as exc:

        print(
            "[Database] Notice: Could not delete "
            f"Atlas topic score "
            f"({exc.__class__.__name__}): {exc}"
        )

    return False


# ============================================================
# UPDATE TOPIC SCORE
# ============================================================

def update_student_topic_score(
    reg_number: str,
    old_track: str,
    old_topic: str,
    new_track: str,
    new_topic: str,
    new_score: float,
) -> bool:
    """
    Updates an existing topic score.

    If track/topic changes, removes the old score first.
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
            old_topic,
        )

    return add_student_topic_score(
        reg_number,
        new_track,
        new_topic,
        new_score,
    )


# ============================================================
# INSTRUCTOR DATA
# ============================================================

def fetch_instructor_data() -> Tuple[Dict[str, Any], str]:
    """
    Fetches instructor data from MongoDB Atlas.

    Supports both:
        insturctor
        instructor
    """

    try:

        db = _get_database()

        for cname in [
            INSTRUCTOR_COLLECTION_NAME,
            "instructor",
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
                        "[Database] Successfully "
                        "retrieved instructor data "
                        f"from MongoDB Atlas "
                        f"({DB_NAME}.{cname})"
                    )

                    return doc, "MongoDB Atlas"

    except Exception as exc:

        print(
            "[Database] Notice: Atlas instructor "
            f"fetch skipped "
            f"({exc.__class__.__name__})."
        )

    return {}, "None"


def _track_to_key(track_name: str) -> str:
    """
    Converts a track name to its MongoDB instructor key.
    """

    t = track_name.strip().lower()

    if "devops" in t:
        return "devops_track"

    return "ai_track"


# ============================================================
# INSTRUCTOR TOPIC STATUS
# ============================================================

def update_instructor_topic_status(
    topic_name: str,
    track_name: str,
    new_status: str,
    completed_date: Optional[str] = None,
) -> bool:
    """
    Updates the status of a topic in the instructor document.
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

        db = _get_database()

        updated = False

        for cname in [
            INSTRUCTOR_COLLECTION_NAME,
            "instructor",
        ]:

            if cname not in db.list_collection_names():
                continue

            doc = db[cname].find_one({})

            if not doc or track_key not in doc:
                continue

            topics = (
                doc[track_key].get("topics", [])
            )

            matched = False

            for topic in topics:

                if (
                    topic.get("topic", "")
                    .strip()
                    .lower()
                    == topic_clean
                ):

                    topic["status"] = status_lower

                    if completed_date is not None:

                        topic["date_completed"] = (
                            completed_date
                        )

                    matched = True

                    break

            if matched:

                db[cname].update_one(
                    {
                        "_id": doc["_id"]
                    },
                    {
                        "$set": {
                            f"{track_key}.topics":
                                topics
                        }
                    }
                )

                print(
                    f"[Database] Updated topic "
                    f"'{topic_name}' status to "
                    f"'{new_status}' in Atlas "
                    f"({cname})"
                )

                updated = True

        return updated

    except Exception as exc:

        print(
            "[Database] Notice: Could not update "
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
    date: Optional[str] = None,
) -> bool:
    """
    Appends a new topic to the instructor document.
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
        "date_completed": date or "",
    }

    try:

        db = _get_database()

        updated = False

        for cname in [
            INSTRUCTOR_COLLECTION_NAME,
            "instructor",
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
                    f"[Database] Added new topic "
                    f"'{topic_name}' to Atlas "
                    f"({cname})"
                )

                updated = True

        return updated

    except Exception as exc:

        print(
            "[Database] Notice: Could not append "
            "topic to Atlas "
            f"({exc.__class__.__name__})."
        )

    return False


# ============================================================
# ASSIGNMENTS
# ============================================================

def fetch_student_assignments_from_mongo(
    register_number: str,
) -> List[Dict[str, Any]]:
    """
    Fetches assignments for a specific student.

    Assignments are stored directly on the student's own document
    in the `Assignments` array.

    There is NO separate assignments collection.
    """

    try:

        coll = _get_students_collection()

        _ensure_indexes()

        doc = coll.find_one(
            _student_query(register_number),
            {
                "_id": 0,
                "Assignments": 1,
            }
        )

        if doc:

            assignments = (
                doc.get("Assignments") or []
            )

            print(
                f"[Database] Retrieved "
                f"{len(assignments)} assignments "
                f"for {register_number} "
                f"from Cluster0 "
                f"({DB_NAME}.{COLLECTION_NAME})"
            )

            return assignments

    except Exception as exc:

        print(
            "[Database] Notice: Could not fetch "
            "assignments from Cluster0 Atlas "
            f"({exc.__class__.__name__})."
        )

    return []


# ============================================================
# SYNC ASSIGNMENT TO STUDENT RECORD
# ============================================================

def sync_assignment_to_student_record(
    reg_number: str,
    track: str,
    topic_name: str,
    score: Optional[float] = None,
) -> bool:
    """
    Syncs an assignment topic into:

        Scores.[AI|DevOps]

    and records it under:

        AssignmentTopics.[AI|DevOps]

    AssignmentTopics allows the application to distinguish
    assignment-derived scores from genuine assessment scores.
    """

    track_key = (
        "DevOps"
        if "devops" in track.lower()
        else "AI"
    )

    topic_clean = topic_name.strip()

    score_val = (
        round(float(score), 2)
        if (
            score is not None
            and score != ""
        )
        else None
    )

    try:

        coll = _get_students_collection()

        _ensure_indexes()

        doc = coll.find_one(
            _student_query(reg_number)
        )

        if not doc:
            return False

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
                },
            }
        )

        print(
            f"[Database] Synced assignment "
            f"'{topic_clean}' "
            f"(Score: {score_val}) "
            f"to student {reg_number} in Atlas"
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
    assignment_id: str,
    old_track: str,
    old_topic: str,
    new_track: str,
    new_topic: str,
    new_score: Optional[float] = None,
    new_status: Optional[str] = None,
) -> bool:
    """
    Updates an existing entry in the student's `Assignments`
    array.

    The assignment remains tagged as assignment-derived data.

    It does NOT become a genuine assessment score.
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

    old_topic_clean = old_topic.strip()
    new_topic_clean = new_topic.strip()

    matched = False

    try:

        coll = _get_students_collection()

        _ensure_indexes()

        doc = coll.find_one(
            _student_query(reg_number)
        )

        if not doc:
            return False

        assignments = (
            doc.get("Assignments") or []
        )

        for assignment in assignments:

            same_id = (
                assignment_id
                and assignment.get("assignmentId")
                == assignment_id
            )

            same_topic = (
                not assignment_id
                and (
                    assignment.get("topic") or ""
                ).strip().lower()
                == old_topic_clean.lower()
                and (
                    assignment.get("track")
                    or "AI"
                )
                == old_track_key
            )

            if same_id or same_topic:

                assignment["topic"] = (
                    new_topic_clean
                )

                assignment["track"] = (
                    new_track_key
                )

                if new_status is not None:

                    assignment["status"] = (
                        new_status
                    )

                if new_score is not None:

                    assignment["score"] = (
                        new_score
                    )

                matched = True

                break

        if matched:

            coll.update_one(
                {
                    "_id": doc["_id"]
                },
                {
                    "$set": {
                        "Assignments":
                            assignments
                    }
                }
            )

        # ----------------------------------------------------
        # Remove stale topic
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
                    },
                }
            )

        print(
            f"[Database] Updated assignment "
            f"'{new_topic_clean}' "
            f"for {reg_number} in Atlas "
            f"(Assignments array)"
        )

    except Exception as exc:

        print(
            "[Database] Notice: Could not update "
            "assignment in Atlas "
            f"({exc.__class__.__name__})."
        )

        matched = False

    # Re-sync under the new track/topic.
    sync_assignment_to_student_record(
        reg_number,
        new_track_key,
        new_topic_clean,
        new_score,
    )

    return matched


# ============================================================
# DELETE ASSIGNMENT
# ============================================================

def delete_assignment_in_mongo(
    reg_number: str,
    assignment_id: str,
    track: str,
    topic: str,
) -> bool:
    """
    Removes an assignment from:

        Assignments

    and clears:

        Scores.[AI|DevOps]
        AssignmentTopics.[AI|DevOps]

    Also removes the corresponding assessment records
    from the legacy assessment collections if present.
    """

    track_key = (
        "DevOps"
        if "devops" in track.lower()
        else "AI"
    )

    topic_clean = topic.strip()

    try:

        db = _get_database()

        coll = db[COLLECTION_NAME]

        _ensure_indexes()

        doc = coll.find_one(
            _student_query(reg_number)
        )

        if not doc:
            return False

        assignments = (
            doc.get("Assignments") or []
        )

        remaining = [
            assignment
            for assignment in assignments
            if not (
                (
                    assignment_id
                    and assignment.get("assignmentId")
                    == assignment_id
                )
                or (
                    not assignment_id
                    and (
                        assignment.get("topic")
                        or ""
                    ).strip().lower()
                    == topic_clean.lower()
                    and (
                        assignment.get("track")
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
                    "Assignments":
                        remaining
                },

                "$unset": {
                    f"Scores.{track_key}.{topic_clean}":
                        ""
                },

                "$pull": {
                    f"AssignmentTopics.{track_key}":
                        topic_clean
                },
            }
        )

        # Legacy assessment collections.
        for assessment_collection in [
            "assesement",
            "assessment",
        ]:

            db[assessment_collection].delete_many(
                {
                    "RegNumber": reg_number.strip(),
                    "Track": track_key,
                    "Topic": topic_clean,
                }
            )

        print(
            f"[Database] Deleted assignment "
            f"'{topic_clean}' for {reg_number} "
            f"in Atlas (Assignments array)"
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
    Saves assignment records directly onto each student's
    own document in the `Assignments` array.

    No separate assignments collection is used.

    Also synchronizes assignment scores into Scores and
    AssignmentTopics.
    """

    if not assignments_list:
        return True

    # --------------------------------------------------------
    # Clean assignment item
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Merge assignments
    # --------------------------------------------------------

    def _merge(
        existing_asgs: List[Dict[str, Any]],
        new_items: List[Dict[str, Any]],
        reg_no: str,
    ) -> List[Dict[str, Any]]:

        asg_map = {
            assignment.get("assignmentId"):
                assignment
            for assignment in existing_asgs
            if assignment.get("assignmentId")
        }

        for item in new_items:

            assignment_id = item.get(
                "assignmentId"
            )

            item_clean = _clean_item(
                item,
                reg_no
            )

            if assignment_id:

                asg_map[assignment_id] = (
                    item_clean
                )

            else:

                existing_asgs.append(
                    item_clean
                )

        return (
            list(asg_map.values())
            if asg_map
            else existing_asgs
        )

    # --------------------------------------------------------
    # Group assignments by student
    # --------------------------------------------------------

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

        coll = _get_students_collection()

        _ensure_indexes()

        # ----------------------------------------------------
        # Save assignments onto each student document
        # ----------------------------------------------------

        for reg_no, new_items in grouped.items():

            student_doc = coll.find_one(
                _student_query(reg_no)
            )

            if not student_doc:

                print(
                    "[Database] Notice: No student "
                    f"found with RegNumber '{reg_no}' "
                    "in Cluster0 — assignment(s) "
                    "not saved."
                )

                continue

            existing_asgs = (
                student_doc.get(
                    "Assignments"
                ) or []
            )

            updated_asg_list = _merge(
                existing_asgs,
                new_items,
                reg_no,
            )

            coll.update_one(
                {
                    "_id":
                        student_doc["_id"]
                },
                {
                    "$set": {
                        "Assignments":
                            updated_asg_list
                    }
                }
            )

            print(
                f"[Database] Saved "
                f"{len(updated_asg_list)} "
                f"assignment(s) on {reg_no}'s "
                f"document in Atlas "
                f"({DB_NAME}.{COLLECTION_NAME})"
            )

            saved_ok = True

    except Exception as exc:

        print(
            "[Database] Notice: Could not save "
            "assignments to Cluster0 Atlas "
            f"({exc.__class__.__name__})."
        )

    # --------------------------------------------------------
    # Sync assignment scores
    # --------------------------------------------------------

    for item in assignments_list:

        reg = (
            item.get("studentRegisterNumber")
            or item.get("regNo")
        )

        track = item.get(
            "track",
            "AI"
        )

        topic = item.get(
            "topic"
        )

        score = item.get(
            "score"
        )

        if reg and topic:

            sync_assignment_to_student_record(
                reg,
                track,
                topic,
                score,
            )

    return saved_ok