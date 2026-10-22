import os
import sys
import json
from datetime import datetime, timedelta
import ist_time
import db_mongo
import fb_publisher

# Defined Strategic Time Slots for 7-Day Experiment (in Indian Standard Time)
EXPERIMENT_SLOTS = [
    {"slot_id": "morning_rush", "name": "Morning Rush", "time_ist": "09:30", "hour": 9, "minute": 30},
    {"slot_id": "lunch_break", "name": "Lunch Break", "time_ist": "14:00", "hour": 14, "minute": 0},
    {"slot_id": "evening_prime", "name": "Evening Prime", "time_ist": "18:30", "hour": 18, "minute": 30},
    {"slot_id": "night_peak", "name": "Night Peak", "time_ist": "21:30", "hour": 21, "minute": 30}
]

STATE_FILE = os.path.join(os.path.dirname(__file__), "logs", "cadence_experiment.json")

def load_experiment_state():
    # 1. Check MongoDB Atlas
    db = db_mongo.get_db()
    if db is not None:
        try:
            doc = db["best_find_experiments"].find_one({"_id": "cadence_7day_v1"})
            if doc:
                doc.pop("_id", None)
                return doc
        except Exception:
            pass

    # 2. Check local file
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Default Initial State: Start 7-Day Experiment Today
    start_dt = ist_time.now_ist()
    end_dt = start_dt + timedelta(days=7)
    initial_state = {
        "status": "RUNNING_EXPERIMENT",
        "phase": "7_DAY_EXPERIMENT",
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),
        "total_days": 7,
        "current_day": 1,
        "winning_slot": None,
        "slots_performance": {
            s["slot_id"]: {
                "name": s["name"],
                "time_ist": s["time_ist"],
                "posts_count": 0,
                "total_views": 0,
                "total_likes": 0,
                "total_comments": 0,
                "total_shares": 0,
                "score": 0.0
            } for s in EXPERIMENT_SLOTS
        },
        "posts": []
    }
    save_experiment_state(initial_state)
    return initial_state

def save_experiment_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
    except Exception:
        pass

    db = db_mongo.get_db()
    if db is not None:
        try:
            db["best_find_experiments"].update_one(
                {"_id": "cadence_7day_v1"},
                {"$set": state},
                upsert=True
            )
        except Exception as e:
            print(f"[!] Warning persisting experiment state to MongoDB: {e}")

def get_current_slot():
    """Identifies which experimental slot the current upload falls into."""
    now = ist_time.now_ist()
    current_minutes = now.hour * 60 + now.minute

    best_slot = EXPERIMENT_SLOTS[0]
    min_diff = 99999

    for s in EXPERIMENT_SLOTS:
        slot_min = s["hour"] * 60 + s["minute"]
        diff = abs(current_minutes - slot_min)
        if diff < min_diff:
            min_diff = diff
            best_slot = s

    return best_slot

def get_next_scheduled_run():
    """
    Calculates the next target publishing time in Indian Standard Time (IST).
    During Experiment: moves through the 4 daily slots.
    After Experiment: targets the winning top-performing time slot(s).
    """
    state = load_experiment_state()
    now = ist_time.now_ist()

    # If experiment is completed and winning slot is set, use winning slot
    if state.get("status") == "OPTIMIZED" and state.get("winning_slot"):
        win_id = state["winning_slot"]
        slot_cfg = next((s for s in EXPERIMENT_SLOTS if s["slot_id"] == win_id), EXPERIMENT_SLOTS[2])
        target_today = now.replace(hour=slot_cfg["hour"], minute=slot_cfg["minute"], second=0, microsecond=0)
        if target_today > now:
            return target_today
        else:
            return target_today + timedelta(days=1)

    # During 7-Day Experiment: Find next slot today or first slot tomorrow
    for s in EXPERIMENT_SLOTS:
        candidate = now.replace(hour=s["hour"], minute=s["minute"], second=0, microsecond=0)
        if candidate > now + timedelta(minutes=2):
            return candidate

    # If all slots passed today, return tomorrow's first slot
    first_slot = EXPERIMENT_SLOTS[0]
    return (now + timedelta(days=1)).replace(hour=first_slot["hour"], minute=first_slot["minute"], second=0, microsecond=0)

def record_post_in_experiment(telegram_message_id, fb_video_id, product_name):
    """Logs a newly published video into the experiment tracker."""
    state = load_experiment_state()
    slot = get_current_slot()
    slot_id = slot["slot_id"]

    now = ist_time.now_ist()
    start_dt = datetime.strptime(state["start_date"], "%Y-%m-%d").date()
    days_elapsed = max(1, min(7, (now.date() - start_dt).days + 1))
    state["current_day"] = days_elapsed

    entry = {
        "timestamp": ist_time.now_ist_str(),
        "telegram_message_id": telegram_message_id,
        "fb_video_id": fb_video_id,
        "product_name": product_name,
        "slot_id": slot_id,
        "slot_name": slot["name"],
        "slot_time": slot["time_ist"],
        "views": 0,
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "score": 0.0
    }
    state["posts"].append(entry)

    # Increment slot count
    if slot_id in state["slots_performance"]:
        state["slots_performance"][slot_id]["posts_count"] += 1

    save_experiment_state(state)
    print(f"[📊] 7-Day Experiment: Tagged Post #{telegram_message_id} to [{slot['name']} • {slot['time_ist']}] (Day {days_elapsed}/7)")
    return entry

def refresh_experiment_metrics(config):
    """
    Queries Meta Graph API for all recent experiment videos to update engagement
    and evaluate the winning timeframe.
    """
    state = load_experiment_state()
    updated = False

    for p in state.get("posts", []):
        fb_id = p.get("fb_video_id")
        if fb_id:
            metrics = fb_publisher.get_video_metrics(fb_id, config)
            if metrics:
                p["views"] = metrics.get("views", 0)
                p["likes"] = metrics.get("likes", 0)
                p["comments"] = metrics.get("comments", 0)
                p["shares"] = metrics.get("shares", 0)
                p["score"] = metrics.get("engagement_score", 0)
                updated = True

    # Recalculate slots performance
    for s_id, s_data in state["slots_performance"].items():
        slot_posts = [p for p in state["posts"] if p.get("slot_id") == s_id]
        if slot_posts:
            s_data["posts_count"] = len(slot_posts)
            s_data["total_views"] = sum(p.get("views", 0) for p in slot_posts)
            s_data["total_likes"] = sum(p.get("likes", 0) for p in slot_posts)
            s_data["total_comments"] = sum(p.get("comments", 0) for p in slot_posts)
            s_data["total_shares"] = sum(p.get("shares", 0) for p in slot_posts)
            s_data["score"] = sum(p.get("score", 0) for p in slot_posts) / len(slot_posts)

    # Check if 7 days completed or evaluate winning slot
    now = ist_time.now_ist()
    start_dt = datetime.strptime(state["start_date"], "%Y-%m-%d").date()
    days_elapsed = (now.date() - start_dt).days + 1
    state["current_day"] = min(7, max(1, days_elapsed))

    # Determine highest scoring slot
    ranked = sorted(state["slots_performance"].items(), key=lambda x: x[1]["score"], reverse=True)
    if ranked:
        best_id, best_data = ranked[0]
        state["winning_slot"] = best_id
        state["winning_slot_name"] = f"{best_data['name']} ({best_data['time_ist']} IST)"

    if days_elapsed >= 7:
        state["status"] = "OPTIMIZED"
        state["phase"] = "AUTONOMOUS_OPTIMIZED_CADENCE"
        print(f"[🏆] 7-DAY CADENCE OPTIMIZER FINISHED! Winning Timeframe: {state.get('winning_slot_name')}")

    save_experiment_state(state)
    return state
