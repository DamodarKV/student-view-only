"""
backfill_assignment_topics.py
------------------------------
One-off migration. Run this ONCE after deploying the AssignmentTopics fix.

Problem it fixes:
    Before this fix, sync_assignment_to_student_record() wrote assignment
    scores into Scores.[AI|DevOps].<topic> but never recorded that the topic
    came from an assignment. Because of that, existing documents have
    assignment-derived entries in Scores that look identical to genuine
    assessment scores — so they show up as bars (often 0-height, since many
    were never actually scored) on the student profile's module bar-graph
    charts (e.g. "Amazon Backend", "Tokeniser Algorithm", "RAG").

What this script does:
    For every student document, it cross-references the `Assignment` array
    (the real list of assigned topics) against `Scores.AI` / `Scores.DevOps`.
    Any Scores key that matches an assignment topic (case-insensitively) gets
    added to AssignmentTopics.[AI|DevOps] via $addToSet. It does NOT delete
    or modify anything in Scores — it only adds the tag data.py's module bar
    charts already rely on to filter these out (hasScore == True check).

    Safe to run multiple times ($addToSet is idempotent).

Usage:
    python backfill_assignment_topics.py            # apply changes
    python backfill_assignment_topics.py --dry-run   # preview only, no writes
"""
import sys

from insert_mongo import get_mongo_client, DB_NAME, COLLECTION_NAME


def backfill(dry_run: bool = False) -> None:
    client = get_mongo_client()
    db = client[DB_NAME]
    coll = db[COLLECTION_NAME]

    docs = list(coll.find({}))
    print(f"[Backfill] Scanning {len(docs)} student document(s) in {DB_NAME}.{COLLECTION_NAME}...")

    total_tagged = 0
    students_touched = 0

    for doc in docs:
        reg = doc.get("RegNumber", "<unknown>")
        assignments = doc.get("Assignment") or []
        scores = doc.get("Scores") or {}

        if not assignments or not scores:
            continue

        # Build {track_key: {topic_lower, ...}} from the Assignment array.
        assignment_topics_by_track = {"AI": set(), "DevOps": set()}
        for a in assignments:
            topic = (a.get("topic") or "").strip()
            track_raw = (a.get("track") or "AI").strip()
            track_key = "DevOps" if "devops" in track_raw.lower() else "AI"
            if topic:
                assignment_topics_by_track[track_key].add(topic.lower())

        existing_tags = doc.get("AssignmentTopics") or {}
        to_add = {"AI": [], "DevOps": []}

        for track_key in ("AI", "DevOps"):
            score_keys = (scores.get(track_key) or {}).keys()
            already_tagged = {t.strip().lower() for t in (existing_tags.get(track_key) or [])}
            for key in score_keys:
                key_lower = key.strip().lower()
                if key_lower in assignment_topics_by_track[track_key] and key_lower not in already_tagged:
                    to_add[track_key].append(key)

        if not to_add["AI"] and not to_add["DevOps"]:
            continue

        students_touched += 1
        tagged_here = len(to_add["AI"]) + len(to_add["DevOps"])
        total_tagged += tagged_here

        print(f"  {reg}: tagging {tagged_here} topic(s) -> AI: {to_add['AI']}, DevOps: {to_add['DevOps']}")

        if dry_run:
            continue

        update_ops = {}
        if to_add["AI"]:
            update_ops["AssignmentTopics.AI"] = {"$each": to_add["AI"]}
        if to_add["DevOps"]:
            update_ops["AssignmentTopics.DevOps"] = {"$each": to_add["DevOps"]}

        coll.update_one({"_id": doc["_id"]}, {"$addToSet": update_ops})

    client.close()

    mode = "DRY RUN — no changes written" if dry_run else "Done"
    print(f"[Backfill] {mode}. Students touched: {students_touched}. Topics tagged: {total_tagged}.")


if __name__ == "__main__":
    backfill(dry_run="--dry-run" in sys.argv)
