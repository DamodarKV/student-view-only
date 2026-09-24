import pandas as pd
import json


# ============================================================
# FILES
# ============================================================

INPUT_FILE = "consolidated_scores_with_training_number.xlsx"
OUTPUT_FILE = "students_output_accuracy_updated3.json"


# ============================================================
# LOAD EXCEL
# ============================================================


from typing import Optional, Dict, Any, Union, List

# ============================================================
# CENTRAL TOPIC REGISTRY & DYNAMIC CALCULATION ENGINE
# ============================================================

import re

DEFAULT_AI_TOPICS = [
    "APIFoldersSQLNoSQL",
    "API",
    "ChromaDB",
    "HuggingFace",
    "LLMTokenization",
    "VectorDB"
]

DEFAULT_DEVOPS_TOPICS = [
    "DevDockerGit",
    "Docker",
    "GitDockerOperations",
    "NGNIX"
]

# Dynamic registry (preserves topic order, initially seeded with defaults)
AI_TOPICS: List[str] = list(DEFAULT_AI_TOPICS)
DEVOPS_TOPICS: List[str] = list(DEFAULT_DEVOPS_TOPICS)


def get_ai_topics() -> List[str]:
    """Returns the list of currently registered AI topics."""
    return list(AI_TOPICS)


def get_devops_topics() -> List[str]:
    """Returns the list of currently registered DevOps topics."""
    return list(DEVOPS_TOPICS)


def get_ai_topic_count() -> int:
    """Returns the dynamic count of registered AI topics (denominator)."""
    return max(len(AI_TOPICS), 1)


def get_devops_topic_count() -> int:
    """Returns the dynamic count of registered DevOps topics (denominator)."""
    return max(len(DEVOPS_TOPICS), 1)


def to_topic_key(name: str) -> str:
    """
    Converts a human-readable topic name (e.g. 'Prompt Engineering') to
    the application's standard PascalCase key ('PromptEngineering').
    """
    words = re.findall(r"[A-Za-z0-9]+", str(name))
    if not words:
        return ""
    return "".join(w[0].upper() + w[1:] if len(w) > 1 else w.upper() for w in words)


def normalize_topic_name(name: str) -> str:
    """
    Normalizes a topic name to lowercase alphanumeric format for
    case-insensitive, whitespace-insensitive duplicate detection.
    """
    if not name:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(name)).lower()


def register_topic(track: str, topic_key: str) -> bool:
    """
    Registers a new topic in the central registry if not already present.
    Returns True if newly added, False if duplicate.
    """
    global AI_TOPICS, DEVOPS_TOPICS
    norm = normalize_topic_name(topic_key)
    if not norm:
        return False

    if "devops" in track.lower():
        if norm not in {normalize_topic_name(t) for t in DEVOPS_TOPICS}:
            DEVOPS_TOPICS.append(topic_key)
            return True
    else:
        if norm not in {normalize_topic_name(t) for t in AI_TOPICS}:
            AI_TOPICS.append(topic_key)
            return True
    return False


def sync_topics_from_students(students_list: List[Dict[str, Any]]):
    """
    Discovers all unique topics present in student documents across the
    collection and registers them in the central registry.
    """
    global AI_TOPICS, DEVOPS_TOPICS
    AI_TOPICS = list(DEFAULT_AI_TOPICS)
    DEVOPS_TOPICS = list(DEFAULT_DEVOPS_TOPICS)
    existing_ai_norm = {normalize_topic_name(t): t for t in AI_TOPICS}
    existing_devops_norm = {normalize_topic_name(t): t for t in DEVOPS_TOPICS}

    for s in students_list:
        scores = s.get("Scores") or s.get("scores") or {}
        if not isinstance(scores, dict):
            continue

        ai_dict = scores.get("AI") or {}
        if isinstance(ai_dict, dict):
            for k in ai_dict.keys():
                norm = normalize_topic_name(k)
                if norm and norm not in existing_ai_norm:
                    existing_ai_norm[norm] = k
                    AI_TOPICS.append(k)

        devops_dict = scores.get("DevOps") or {}
        if isinstance(devops_dict, dict):
            for k in devops_dict.keys():
                norm = normalize_topic_name(k)
                if norm and norm not in existing_devops_norm:
                    existing_devops_norm[norm] = k
                    DEVOPS_TOPICS.append(k)


# ============================================================
# REUSABLE AVERAGE CALCULATION FUNCTIONS
# ============================================================

from decimal import Decimal, ROUND_HALF_UP


def round_score(val: Union[float, int, str, Decimal], decimals: int = 2) -> float:
    try:
        d = Decimal(str(val))
        return float(d.quantize(Decimal("10") ** -decimals, rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def calculate_ai_average(scores: Any) -> float:
    """
    Calculate AI track average using dynamic denominator based on active student AI topics.
    Formula: min(sum(available AI topic scores) / len(valid_topics), 100.0)
    Missing / empty / None topics contribute 0.0 to the numerator.
    Safely handles empty topic lists without division by zero.
    """
    if scores is None:
        return 0.0

    total = 0.0
    valid_topics = []
    if isinstance(scores, dict):
        ai_dict = scores.get("AI") if isinstance(scores.get("AI"), dict) else scores
        norm_devops = {normalize_topic_name(t) for t in DEVOPS_TOPICS}
        for topic, val in ai_dict.items():
            if topic == "DevOps" or normalize_topic_name(topic) in norm_devops:
                continue
            valid_topics.append(topic)
            if val is not None and str(val).strip() != "":
                try:
                    total += float(val)
                except (ValueError, TypeError):
                    pass
        denom = len(valid_topics)
    elif isinstance(scores, (list, tuple)):
        for val in scores:
            if val is not None and str(val).strip() != "":
                try:
                    total += float(val)
                except (ValueError, TypeError):
                    pass
        denom = len(scores)
    else:
        try:
            total = float(scores)
        except (ValueError, TypeError):
            return 0.0
        denom = 1

    if denom <= 0:
        return 0.0
    avg = total / denom
    return min(max(round_score(avg, 2), 0.0), 100.0)


def calculate_devops_average(scores: Any) -> float:
    """
    Calculate DevOps track average using dynamic denominator based on active student DevOps topics.
    Formula: min(sum(available DevOps topic scores) / len(valid_topics), 100.0)
    Missing / empty / None topics contribute 0.0 to the numerator.
    Safely handles empty topic lists without division by zero.
    """
    if scores is None:
        return 0.0

    total = 0.0
    valid_topics = []
    if isinstance(scores, dict):
        devops_dict = scores.get("DevOps") if isinstance(scores.get("DevOps"), dict) else scores
        norm_ai = {normalize_topic_name(t) for t in AI_TOPICS}
        for topic, val in devops_dict.items():
            if topic == "AI" or normalize_topic_name(topic) in norm_ai:
                continue
            valid_topics.append(topic)
            if val is not None and str(val).strip() != "":
                try:
                    total += float(val)
                except (ValueError, TypeError):
                    pass
        denom = len(valid_topics)
    elif isinstance(scores, (list, tuple)):
        for val in scores:
            if val is not None and str(val).strip() != "":
                try:
                    total += float(val)
                except (ValueError, TypeError):
                    pass
        denom = len(scores)
    else:
        try:
            total = float(scores)
        except (ValueError, TypeError):
            return 0.0
        denom = 1

    if denom <= 0:
        return 0.0
    avg = total / denom
    return min(max(round_score(avg, 2), 0.0), 100.0)


def calculate_aggregate_score(ai_score: Optional[float], devops_score: Optional[float] = None) -> float:
    """
    Calculate student's overall aggregate using:
    Aggregate = (AI Average + DevOps Average) / 2 (if both exist)
    If only one track exists, that track average is returned.
    The result is always clamped between 0 and 100 and rounded to 2 decimals.
    """
    has_ai = ai_score is not None and str(ai_score).strip() != ""
    has_devops = devops_score is not None and str(devops_score).strip() != ""

    try:
        ai_val = float(ai_score) if has_ai else None
    except (ValueError, TypeError):
        ai_val = None

    try:
        devops_val = float(devops_score) if has_devops else None
    except (ValueError, TypeError):
        devops_val = None

    if ai_val is not None and devops_val is not None:
        agg = float((Decimal(str(ai_val)) + Decimal(str(devops_val))) / Decimal("2"))
    elif ai_val is not None:
        agg = ai_val
    elif devops_val is not None:
        agg = devops_val
    else:
        agg = 0.0

    return min(max(round_score(agg, 2), 0.0), 100.0)


def calculate_student_averages_from_tracks(ai_score: Optional[float], devops_score: Optional[float]) -> Dict[str, Any]:
    """
    Calculate track averages and aggregate score when track-level scores are updated.
    """
    has_ai = ai_score is not None and str(ai_score).strip() != ""
    has_devops = devops_score is not None and str(devops_score).strip() != ""

    ai_val = min(max(round_score(float(ai_score), 2), 0.0), 100.0) if has_ai else None
    devops_val = min(max(round_score(float(devops_score), 2), 0.0), 100.0) if has_devops else None
    agg_val = calculate_aggregate_score(ai_val, devops_val)

    return {
        "AI": ai_val,
        "DevOps": devops_val,
        "Aggregate": agg_val
    }


def calculate_student_averages_from_topics(scores_dict: Dict[str, Any], has_devops: Optional[bool] = None) -> Dict[str, Any]:
    """
    Calculate track averages (using fixed denominators 6 and 4) and aggregate score
    from a student's full Scores dictionary.
    Supports both nested {"AI": {...}, "DevOps": {...}} format and flat topic-score dictionaries.
    """
    if not isinstance(scores_dict, dict):
        scores_dict = {}

    if "AI" in scores_dict or "DevOps" in scores_dict:
        ai_scores = scores_dict.get("AI", {}) if isinstance(scores_dict.get("AI"), dict) else {}
        devops_scores = scores_dict.get("DevOps", {}) if isinstance(scores_dict.get("DevOps"), dict) else {}
    else:
        # Flat dictionary mapping topic names to scores
        ai_scores = {}
        devops_scores = {}
        norm_ai = {normalize_topic_name(t) for t in AI_TOPICS}
        norm_devops = {normalize_topic_name(t) for t in DEVOPS_TOPICS}
        for k, v in scores_dict.items():
            k_norm = normalize_topic_name(k)
            if k_norm in norm_ai:
                ai_scores[k] = v
            elif k_norm in norm_devops:
                devops_scores[k] = v

    ai_avg = calculate_ai_average(ai_scores)

    if has_devops is None:
        has_devops = bool(devops_scores)

    devops_avg = calculate_devops_average(devops_scores) if has_devops else None
    agg = calculate_aggregate_score(ai_avg, devops_avg)

    return {
        "AI": ai_avg,
        "DevOps": devops_avg,
        "Aggregate": agg
    }

# ============================================================
# HELPER FUNCTION
# ============================================================

def get_value(data, column):
    """
    Get the first valid value from a column
    for the current student.
    """

    # Column does not exist
    if column not in data.columns:
        return ""

    for value in data[column]:

        # Ignore NaN
        if pd.isna(value):
            continue

        value = str(value).strip()

        # Ignore empty values
        if value == "":
            continue

        # Ignore string 'nan'
        if value.lower() == "nan":
            continue

        return value

    return ""


# ============================================================
# ACCURACY CONVERSION
# ============================================================

def convert_accuracy(value):
    """
    Convert Accuracy into a percentage number.

    Examples:

        50%     -> 50
        77%     -> 77
        0.85    -> 85
        0.6667  -> 66.67
        100     -> 100
        blank   -> None
    """

    # Missing value
    if pd.isna(value):
        return None

    value = str(value).strip()

    # Empty value
    if value == "":
        return None

    # Check whether original value contained %
    has_percent = "%" in value

    # Remove %
    value = value.replace("%", "").strip()

    # Convert to number
    number = pd.to_numeric(
        value,
        errors="coerce"
    )

    # Invalid number
    if pd.isna(number):
        return None

    number = float(number)

    # --------------------------------------------------------
    # Decimal percentage conversion
    # --------------------------------------------------------
    #
    # 0.85   -> 85
    # 0.6667 -> 66.67
    #
    # But:
    #
    # 85% -> 85
    #
    # --------------------------------------------------------

    if not has_percent and 0 <= number <= 1:
        number = number * 100

    number = round(number, 2)

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if number < 0 or number > 100:

        print(
            "\nWARNING: Invalid accuracy found:",
            number
        )

        return None

    # 100.0 -> 100
    if number.is_integer():
        return int(number)

    return number




def run_pipeline():
    df = pd.read_excel(INPUT_FILE)

    print("Excel file loaded successfully")

    # Remove spaces from column names
    df.columns = df.columns.str.strip()

    print("\nAvailable columns:")
    print(df.columns.tolist())


    # ============================================================
    # REQUIRED COLUMNS
    # ============================================================

    required_columns = [
        "Training Number",
        "First Name",
        "Last Name",
        "Branch",
        "Subject",
        "Accuracy"
    ]


    for column in required_columns:

        if column not in df.columns:

            raise ValueError(
                f"Required column is missing from Excel file: {column}"
            )


    # ============================================================
    # CLEAN TRAINING NUMBER
    # ============================================================

    df["Training Number"] = df["Training Number"].apply(
        lambda value:
            str(value).strip()
            if pd.notna(value)
            else ""
    )


    # ============================================================
    # FIND ATTENDANCE COLUMNS
    # ============================================================

    attendance_columns = []

    for column in df.columns:

        column_name = str(column).strip()

        if column_name.lower().startswith("day"):

            attendance_columns.append(column_name)


    print("\nAttendance columns found:")

    if attendance_columns:

        print(attendance_columns)

    else:

        print("No attendance columns found")


    # ============================================================
    # FIND ALL AVAILABLE TOPICS
    # ============================================================
    #
    # IMPORTANT:
    #
    # We discover the topics from the complete Excel file.
    #
    # We do NOT hardcode:
    #
    # AI = 5
    # DevOps = 3
    #
    # Current Excel gives:
    #
    # AI = 6
    # DevOps = 4
    #
    # Networks is ignored because it is not part of Scores.
    #
    # ============================================================

    all_topics = {
        "AI": set(),
        "DevOps": set()
    }


    for _, row in df.iterrows():

        subject = row["Subject"]

        if pd.isna(subject):
            continue

        subject = str(subject).strip()

        if subject == "":
            continue

        if "_" not in subject:
            continue


        # --------------------------------------------------------
        # Split from LAST underscore
        # --------------------------------------------------------
        #
        # Example:
        #
        # APIFoldersSQLNoSQL_AI
        #
        # becomes:
        #
        # APIFoldersSQLNoSQL
        # AI
        #
        # --------------------------------------------------------

        topic, track = subject.rsplit("_", 1)

        topic = topic.strip()
        track = track.strip()


        # Only AI and DevOps
        if track not in all_topics:
            continue


        all_topics[track].add(topic)


    # ============================================================
    # CONVERT TOPIC SETS TO SORTED LISTS
    # ============================================================

    ai_topics = sorted(
        all_topics["AI"]
    )

    devops_topics = sorted(
        all_topics["DevOps"]
    )


    # ============================================================
    # FIXED TOPIC COUNTS
    # ============================================================
    # IMPORTANT:
    # The denominators are fixed and MUST NOT depend on the
    # number of topics discovered in Excel.
    #
    # AI     = always 6
    # DevOps = always 4
    # Total  = always 10
    #
    # Missing topic scores do not reduce the denominator.
    # ============================================================

    print("Expected AI topics:", AI_TOPICS)
    print("Expected DevOps topics:", DEVOPS_TOPICS)
    print("Discovered AI topics:", ai_topics)
    print("Discovered DevOps topics:", devops_topics)

    print("Fixed AI topic count:", AI_TOPIC_COUNT)
    print("Fixed DevOps topic count:", DEVOPS_TOPIC_COUNT)
    print("Fixed total topic count:", TOTAL_TOPIC_COUNT)

    missing_ai_topics = set(AI_TOPICS) - set(ai_topics)
    missing_devops_topics = set(DEVOPS_TOPICS) - set(devops_topics)

    if missing_ai_topics:
        print("WARNING - Missing AI topics in Excel:", sorted(missing_ai_topics))

    if missing_devops_topics:
        print("WARNING - Missing DevOps topics in Excel:", sorted(missing_devops_topics))


    # ============================================================
    # PRINT TOPIC INFORMATION
    # ============================================================

    print("\n========================================")
    print("TOPICS FOUND")
    print("========================================")

    print("\nAI topics:")

    for topic in ai_topics:
        print(" -", topic)

    print(
        "AI topic count:",
        AI_TOPIC_COUNT
    )


    print("\nDevOps topics:")

    for topic in devops_topics:
        print(" -", topic)

    print(
        "DevOps topic count:",
        DEVOPS_TOPIC_COUNT
    )


    print(
        "\nTotal AI + DevOps topics:",
        TOTAL_TOPIC_COUNT
    )


    # ============================================================
    # UNIQUE STUDENTS
    # ============================================================

    unique_training_numbers = (
        df.loc[
            df["Training Number"] != "",
            "Training Number"
        ]
        .unique()
    )


    print(
        "\nTotal students found:",
        len(unique_training_numbers)
    )


    # ============================================================
    # STUDENT PROCESSING
    # ============================================================

    students = []


    for training_number in unique_training_numbers:

        print(
            "\nProcessing:",
            training_number
        )


        # ========================================================
        # GET THIS STUDENT'S ROWS
        # ========================================================

        student_data = df[
            df["Training Number"] == training_number
        ].copy()


        # ========================================================
        # BASIC STUDENT INFORMATION
        # ========================================================

        first_name = get_value(
            student_data,
            "First Name"
        )

        last_name = get_value(
            student_data,
            "Last Name"
        )


        # --------------------------------------------------------
        # StudentName comes from First Name + Last Name
        #
        # NOT Player Name
        # NOT USN Number
        # --------------------------------------------------------

        student_name = (
            f"{first_name} {last_name}"
        ).strip()


        branch = get_value(
            student_data,
            "Branch"
        )


        # --------------------------------------------------------
        # Mail ID and Mobile Number are optional
        #
        # Your current Excel does not have these columns.
        # Therefore they will become "".
        # --------------------------------------------------------

        mail_id = get_value(
            student_data,
            "Mail ID"
        )

        mobile_number = get_value(
            student_data,
            "Mobile Number"
        )


        # ========================================================
        # SCORE STRUCTURE
        # ========================================================

        scores = {
            "AI": {},
            "DevOps": {}
        }


        # ========================================================
        # READ EVERY EXAM ROW FOR THIS STUDENT
        # ========================================================

        for _, row in student_data.iterrows():

            subject = row["Subject"]


            # ----------------------------------------------------
            # Ignore missing subject
            # ----------------------------------------------------

            if pd.isna(subject):
                continue


            subject = str(subject).strip()


            if subject == "":
                continue


            # ----------------------------------------------------
            # Subject must contain "_"
            # ----------------------------------------------------

            if "_" not in subject:
                continue


            # ----------------------------------------------------
            # Split only at LAST underscore
            # ----------------------------------------------------

            topic, track = subject.rsplit(
                "_",
                1
            )


            topic = topic.strip()
            track = track.strip()


            # ----------------------------------------------------
            # Ignore tracks other than AI / DevOps
            #
            # Networks will therefore not enter Scores.
            # ----------------------------------------------------

            if track not in scores:
                continue


            # ----------------------------------------------------
            # Convert Accuracy
            # ----------------------------------------------------

            accuracy = convert_accuracy(
                row["Accuracy"]
            )


            # ----------------------------------------------------
            # Store score
            #
            # None becomes null in JSON.
            #
            # Example:
            #
            # "RAG": null
            # ----------------------------------------------------

            scores[track][topic] = accuracy


        # ========================================================
        # AI TOTAL
        # ========================================================
        #
        # IMPORTANT:
        #
        # We iterate through ALL AI topics found in Excel.
        #
        # If the student doesn't have the topic:
        #
        #     0
        #
        # If the student has:
        #
        #     topic = None
        #
        # it also contributes:
        #
        #     0
        #
        # ========================================================

        ai_total = 0


        for topic in ai_topics:

            value = scores["AI"].get(
                topic,
                None
            )


            if value is None:
                continue


            try:

                ai_total += float(value)

            except (
                ValueError,
                TypeError
            ):

                continue


        # ========================================================
        # DEVOPS TOTAL
        # ========================================================

        devops_total = 0


        for topic in devops_topics:

            value = scores["DevOps"].get(
                topic,
                None
            )


            if value is None:
                continue


            try:

                devops_total += float(value)

            except (
                ValueError,
                TypeError
            ):

                continue


        # ========================================================
        # AI AVERAGE
        # ========================================================
        #
        # Current Excel:
        #
        # AI = 6 topics
        #
        # Example:
        #
        # Student has:
        #
        # 100, 90, null, null, null, null
        #
        # Calculation:
        #
        # 100 + 90 + 0 + 0 + 0 + 0
        # -------------------------
        #            6
        #
        # ========================================================

        if scores["AI"]:

            # ALWAYS divide by 6, even when some AI topics are missing.
            ai_average = ai_total / AI_TOPIC_COUNT

        else:

            ai_average = ""

        if ai_average != "":
            ai_average = round(
                max(0.0, min(ai_average, 100.0)),
                2
            )


        # ========================================================
        # DEVOPS AVERAGE
        # ========================================================
        #
        # Current Excel:
        #
        # DevOps = 4 topics
        #
        # Example Pruthvi:
        #
        # Docker              100
        # GitDockerOperations 100
        # NGNIX               100
        # DevDockerGit          0
        #
        # 300 / 4 = 75
        #
        # ========================================================

        if scores["DevOps"]:

            # ALWAYS divide by 4, even when some DevOps topics are missing.
            devops_average = devops_total / DEVOPS_TOPIC_COUNT

        else:

            devops_average = ""

        if devops_average != "":
            devops_average = round(
                max(0.0, min(devops_average, 100.0)),
                2
            )


        # ========================================================
        # AGGREGATE AVERAGE
        # ========================================================
        #
        # Equal weighting: AI 50% + DevOps 50%
        # Overall Average = (AI Average + DevOps Average) / 2
        #
        # ========================================================

        has_ai_avg = ai_average != ""
        has_do_avg = devops_average != ""

        if has_ai_avg and has_do_avg:
            aggregate_average = (ai_average + devops_average) / 2.0
        elif has_ai_avg:
            aggregate_average = ai_average
        elif has_do_avg:
            aggregate_average = devops_average
        else:
            aggregate_average = ""


        # ========================================================
        # ROUND AVERAGES
        # ========================================================

        if ai_average != "":
            ai_average = round(
                ai_average,
                2
            )


        if devops_average != "":
            devops_average = round(
                devops_average,
                2
            )


        if aggregate_average != "":
            aggregate_average = round(
                aggregate_average,
                2
            )


        # ========================================================
        # SAFETY CHECK
        # ========================================================
        #
        # An average should NEVER be:
        #
        # > 100
        #
        # If it happens, stop the program.
        #
        # ========================================================

        averages = {
            "AI": ai_average,
            "DevOps": devops_average,
            "Aggregate": aggregate_average
        }


        for average_name, average_value in averages.items():

            if average_value != "":

                if not (
                    0 <= average_value <= 100
                ):

                    raise ValueError(
                        f"Invalid {average_name} average "
                        f"{average_value} for "
                        f"{training_number}"
                    )


        # ========================================================
        # ATTENDANCE
        # ========================================================

        attendance = {}


        for day_column in attendance_columns:

            attendance_value = get_value(
                student_data,
                day_column
            )

            attendance[day_column] = attendance_value


        # ========================================================
        # STATUS
        # ========================================================

        track_status = ""

        watch_list_status = ""


        # ========================================================
        # FINAL STUDENT OBJECT
        # ========================================================

        student = {

            "StudentName": student_name,

            "RegNumber": training_number,

            "MailID": mail_id,

            "MobileNumber": mobile_number,

            "Branch": branch,

            "Scores": scores,

            "AverageScore": {

                "AI": ai_average,

                "DevOps": devops_average,

                "Aggregate": aggregate_average
            },

            "Attandance": attendance,

            "TrackStatus": track_status,

            "WatchListStatus": watch_list_status
        }


        # Add student
        students.append(student)


    # ============================================================
    # SAVE JSON
    # ============================================================

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            students,
            file,
            indent=4,
            ensure_ascii=False
        )


    # ============================================================
    # FINAL OUTPUT
    # ============================================================

    print("\n========================================")
    print("JSON CREATION COMPLETED SUCCESSFULLY")
    print("========================================")

    print(
        "Total students:",
        len(students)
    )

    print(
        "AI topics:",
        AI_TOPIC_COUNT
    )

    print(
        "DevOps topics:",
        DEVOPS_TOPIC_COUNT
    )

    print(
        "Total topics:",
        TOTAL_TOPIC_COUNT
    )

    print(
        "Attendance columns:",
        attendance_columns
    )

    print(
        "Output file:",
        OUTPUT_FILE
    )

if __name__ == "__main__":
    run_pipeline()
