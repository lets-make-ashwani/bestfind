import os
import sys
import ist_time

# UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_client = None
_db = None
_mongo_uri_checked = None

def get_mongo_uri():
    return os.environ.get("MONGODB_URI", os.environ.get("MONGO_URI", "")).strip()

def get_db():
    global _client, _db, _mongo_uri_checked
    uri = get_mongo_uri()
    if not uri:
        return None

    if _db is not None and _mongo_uri_checked == uri:
        return _db

    try:
        from pymongo import MongoClient
        import urllib.parse
        _client = MongoClient(uri, serverSelectionTimeoutMS=4000)
        _client.admin.command('ping')
        db_name = os.environ.get("MONGODB_DATABASE", "best_find")
        _db = _client[db_name]
        _mongo_uri_checked = uri
        return _db
    except Exception as e:
        print(f"[!] MongoDB Atlas connection notice: {e}")
        return None

def is_connected():
    return get_db() is not None

def load_tracker_from_mongo():
    db = get_db()
    if db is None:
        return None
    try:
        doc = db["best_find_tracker"].find_one({"channel": "allterapost"})
        if doc:
            return {
                "channel": doc.get("channel", "allterapost"),
                "last_processed_id": doc.get("last_processed_id", 28824),
                "processed_ids": doc.get("processed_ids", [])
            }
    except Exception as e:
        print(f"[!] Error loading tracker from MongoDB: {e}")
    return None

def save_tracker_to_mongo(tracker_data):
    db = get_db()
    if db is None:
        return False
    try:
        payload = {
            "channel": tracker_data.get("channel", "allterapost"),
            "last_processed_id": tracker_data.get("last_processed_id", 28824),
            "processed_ids": tracker_data.get("processed_ids", []),
            "updated_at": ist_time.now_ist_str()
        }
        db["best_find_tracker"].update_one(
            {"channel": payload["channel"]},
            {"$set": payload},
            upsert=True
        )
        return True
    except Exception as e:
        print(f"[!] Error saving tracker to MongoDB: {e}")
        return False

def save_post_to_mongo(post_data):
    db = get_db()
    if db is None:
        return False
    try:
        db["best_find_posts"].insert_one(post_data)
        return True
    except Exception as e:
        print(f"[!] Error saving post to MongoDB: {e}")
        return False

def load_recent_history_from_mongo(limit=10):
    db = get_db()
    if db is None:
        return None
    try:
        cursor = db["best_find_posts"].find({}, {"_id": 0}).sort("timestamp", -1).limit(limit)
        return list(cursor)
    except Exception as e:
        print(f"[!] Error loading history from MongoDB: {e}")
        return None
