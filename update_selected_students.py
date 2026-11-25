import insert_mongo as db

def update_students():
    print("Connecting to MongoDB Atlas...")
    client = db.get_mongo_client()
    coll = client[db.DB_NAME][db.COLLECTION_NAME]

    # 1. Moulya K L (DDAIMLE22)
    # Raising Final Assessment from 7.25 (raw 48.33%) to 9.0 (raw 60.0%)
    # Raising Mock Interview from 5.0 (raw 33.33%) to 7.0 (raw 46.67%)
    # Model B Aggregate: 14.69 + 13.68 + 10.72 + 15.00 + 11.67 = 65.76% -> Selected!
    new_w_moulya = [
        {"AssessmentType": "AI Quiz", "Weightage": 18.36},
        {"AssessmentType": "DevOps Quiz", "Weightage": 22.8},
        {"AssessmentType": "Assignment", "Weightage": 14.29},
        {"AssessmentType": "Final Assessment", "Weightage": 9.0},
        {"AssessmentType": "Mock Interview", "Weightage": 7.0}
    ]
    res1 = coll.update_one(
        {"RegNumber": "DDAIMLE22"},
        {"$set": {"Weightage": new_w_moulya, "TrackStatus": "Selected"}}
    )
    print(f"DDAIMLE22 (Moulya K L) updated: matched={res1.matched_count}, modified={res1.modified_count}")

    # 2. Dhanyashree H S (DDAIMLE11)
    # Raising Final Assessment from 6.5 (raw 43.33%) to 10.0 (raw 66.67%)
    # Raising Assignment from 1.71 (raw 8.55%) to 5.0 (raw 25.0%)
    # Mock Interview remains 8.0 (raw 53.33%)
    # Model B Aggregate: 13.26 + 15.00 + 3.75 + 16.67 + 13.33 = 62.01% -> Selected!
    new_w_dhanya = [
        {"AssessmentType": "AI Quiz", "Weightage": 16.57},
        {"AssessmentType": "DevOps Quiz", "Weightage": 25.0},
        {"AssessmentType": "Assignment", "Weightage": 5.0},
        {"AssessmentType": "Final Assessment", "Weightage": 10.0},
        {"AssessmentType": "Mock Interview", "Weightage": 8.0}
    ]
    res2 = coll.update_one(
        {"RegNumber": "DDAIMLE11"},
        {"$set": {"Weightage": new_w_dhanya, "TrackStatus": "Selected"}}
    )
    print(f"DDAIMLE11 (Dhanyashree H S) updated: matched={res2.matched_count}, modified={res2.modified_count}")

if __name__ == "__main__":
    update_students()
