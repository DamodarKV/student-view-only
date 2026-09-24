import json
from pathlib import Path
from urllib.parse import quote_plus
from typing import Dict, Any

from pymongo import MongoClient
from pymongo.server_api import ServerApi

try:
    import certifi
except ImportError:
    certifi = None


# ============================================================
# CONFIGURATION
# ============================================================

USERNAME = "damodarkvlearning_db_user"
PASSWORD = "decode3103"

CLUSTER_URL = "@cluster0.vsg22u7.mongodb.net/?appName=Cluster0"

DB_NAME = "Cluster0"
COLLECTION_NAME = "insturctor"  # Requested collection name

BASE_DIR = Path(__file__).resolve().parent
JSON_FILE_PATH = BASE_DIR / "instructor.json"


# ============================================================
# CREATE MONGODB URI & CLIENT
# ============================================================

username = quote_plus(USERNAME)
password = quote_plus(PASSWORD)

uri = f"mongodb+srv://{username}:{password}@cluster0.vsg22u7.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"

mongo_kwargs: Dict[str, Any] = {
    "server_api": ServerApi("1"),
    "serverSelectionTimeoutMS": 5000,
}
if certifi:
    mongo_kwargs["tlsCAFile"] = certifi.where()


def insert_instructor_data():
    # ============================================================
    # LOAD JSON DATA
    # ============================================================
    with open(JSON_FILE_PATH, "r", encoding="utf-8") as file:
        raw_data = json.load(file)

    client = MongoClient(uri, **mongo_kwargs)

    try:
        # Test connection
        client.admin.command("ping")
        print("MongoDB connection successful!")

        db = client[DB_NAME]

        # Collections to populate: 'insturctor' (exact requested name)
        # as well as 'instructor' (normalized spelling) for compatibility
        collections_to_update = [COLLECTION_NAME, "instructor"]

        for coll_name in collections_to_update:
            coll = db[coll_name]

            # Clear existing data to prevent duplicate documents
            deleted = coll.delete_many({})
            print(f"Cleared {deleted.deleted_count} existing document(s) from collection '{coll_name}'.")

            # Deep-copy to avoid modifying the same dict with _id on second insert
            doc_to_insert = json.loads(json.dumps(raw_data))

            result = coll.insert_one(doc_to_insert)
            print(f"Inserted document into '{coll_name}' with _id: {result.inserted_id}")

        print("\nAll instructor data injected successfully!")

    except Exception as e:
        print("MongoDB operation failed:")
        print(e)
        raise e

    finally:
        client.close()
        print("MongoDB connection closed.")


if __name__ == "__main__":
    insert_instructor_data()