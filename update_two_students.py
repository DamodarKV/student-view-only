import insert_mongo as db

def update_students():
    print("Connecting to MongoDB Atlas...")
    client = db.get_mongo_client()
    coll = client[db.DB_NAME][db.COLLECTION_NAME]

    # 1. Shreyas G Nimbalkar (DDAIISE42)
    # Reducing topic scores, average scores, and assessment weightage
    # AverageScore: AI 58.14, DevOps 61.67, Aggregate 59.91
    # Weightages: AI Quiz (wt 11.63), DevOps Quiz (wt 9.25), Assignment (wt 1.12), Final Assessment (wt 8.33), Mock Interview (wt 6.67)
    # Model B Aggregate: 11.63 + 9.25 + 1.12 + 8.33 + 6.67 = 37.00% (< 40!) -> Rejected!
    scores1 = {
        "AI": {
            "API-Basics": 60.0,
            "VectorDB": 65.0,
            "LLMTokenization": 62.0,
            "HuggingFace": 55.0,
            "ChromaDB": 50.0,
            "API-DB-CRUD-Operations": 45.0,
            "PromptEngineering": 70.0
        },
        "DevOps": {
            "Appdev Git": 65.0,
            "DockerInto File": 60.0,
            "Compose Volume Network NGNIX": 60.0
        }
    }
    avg1 = {"AI": 58.14, "DevOps": 61.67, "Aggregate": 59.91}
    weightage1 = [
        {"AssessmentType": "AI Quiz", "Weightage": 14.54},
        {"AssessmentType": "DevOps Quiz", "Weightage": 15.42},
        {"AssessmentType": "Assignment", "Weightage": 1.5},
        {"AssessmentType": "Final Assessment", "Weightage": 5.0},
        {"AssessmentType": "Mock Interview", "Weightage": 4.0}
    ]
    res1 = coll.update_one(
        {"RegNumber": "DDAIISE42"},
        {"$set": {
            "Scores": scores1,
            "AverageScore": avg1,
            "Weightage": weightage1,
            "TrackStatus": "Rejected"
        }}
    )
    print(f"DDAIISE42 (Shreyas G Nimbalkar) updated: matched={res1.matched_count}, modified={res1.modified_count}")

    # 2. Yashavant Shankar Marathi (DDAIISE49)
    # Reducing topic scores, average scores, and assessment weightage
    # AverageScore: AI 57.86, DevOps 61.67, Aggregate 59.77
    # Weightages: AI Quiz (wt 11.57), DevOps Quiz (wt 9.25), Assignment (wt 0.75), Final Assessment (wt 9.17), Mock Interview (wt 6.67)
    # Model B Aggregate: 11.57 + 9.25 + 0.75 + 9.17 + 6.67 = 37.41% (< 40!) -> Rejected!
    scores2 = {
        "AI": {
            "API-Basics": 65.0,
            "VectorDB": 60.0,
            "LLMTokenization": 60.0,
            "HuggingFace": 55.0,
            "ChromaDB": 50.0,
            "API-DB-CRUD-Operations": 50.0,
            "PromptEngineering": 65.0
        },
        "DevOps": {
            "Appdev Git": 60.0,
            "DockerInto File": 65.0,
            "Compose Volume Network NGNIX": 60.0
        }
    }
    avg2 = {"AI": 57.86, "DevOps": 61.67, "Aggregate": 59.77}
    weightage2 = [
        {"AssessmentType": "AI Quiz", "Weightage": 14.47},
        {"AssessmentType": "DevOps Quiz", "Weightage": 15.42},
        {"AssessmentType": "Assignment", "Weightage": 1.0},
        {"AssessmentType": "Final Assessment", "Weightage": 5.5},
        {"AssessmentType": "Mock Interview", "Weightage": 4.0}
    ]
    res2 = coll.update_one(
        {"RegNumber": "DDAIISE49"},
        {"$set": {
            "Scores": scores2,
            "AverageScore": avg2,
            "Weightage": weightage2,
            "TrackStatus": "Rejected"
        }}
    )
    print(f"DDAIISE49 (Yashavant Shankar Marathi) updated: matched={res2.matched_count}, modified={res2.modified_count}")

if __name__ == "__main__":
    update_students()
