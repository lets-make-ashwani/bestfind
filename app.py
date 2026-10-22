import os
import sys
import json
import time
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import log_streamer
log_streamer.init_log_streamer()

import pipeline
import telegram_extractor
import cadence_optimizer
import db_mongo
import config_loader
import ist_time

# UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

app = FastAPI(title="Best Find Automation Cloud Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global State
automation_state = {
    "status": "RUNNING",
    "is_paused": False,
    "last_run": None,
    "next_run": ist_time.format_ts_to_ist(cadence_optimizer.get_next_scheduled_run().strftime("%Y-%m-%d %H:%M:%S")),
    "next_run_iso": cadence_optimizer.get_next_scheduled_run().isoformat(),
    "is_processing": False,
    "last_result": None
}

def load_recent_history():
    mongo_hist = db_mongo.load_recent_history_from_mongo(limit=10)
    if mongo_hist is not None and len(mongo_hist) > 0:
        return mongo_hist

    history_file = os.path.join(os.path.dirname(__file__), "logs", "post_history.json")
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data[-10:][::-1]
        except Exception:
            return []
    return []

def run_cycle_task():
    global automation_state
    if automation_state["is_processing"]:
        print("[!] Cycle already in progress. Skipping.")
        return
    automation_state["is_processing"] = True
    automation_state["last_run"] = ist_time.now_ist_str()
    try:
        success = pipeline.execute_pipeline_cycle()
        automation_state["last_result"] = "Success" if success else "Failed / Retrying"
    except Exception as e:
        print(f"[X] Error during cycle: {e}")
        automation_state["last_result"] = f"Error: {e}"
    finally:
        automation_state["is_processing"] = False
        next_dt = cadence_optimizer.get_next_scheduled_run()
        automation_state["next_run"] = ist_time.format_ts_to_ist(next_dt.strftime("%Y-%m-%d %H:%M:%S"))
        automation_state["next_run_iso"] = next_dt.isoformat()

async def background_cadence_scheduler():
    """
    Background loop that wakes up and posts according to the 7-day
    experiment slots (09:30, 14:00, 18:30, 21:30 IST) or the locked winning slot.
    """
    print("[*] 24/7 Cloud Background Cadence Scheduler Started!")
    await asyncio.sleep(20)
    while True:
        now = ist_time.now_ist()
        next_target = cadence_optimizer.get_next_scheduled_run()
        sleep_sec = max(10, (next_target - now).total_seconds())

        automation_state["next_run"] = ist_time.format_ts_to_ist(next_target.strftime("%Y-%m-%d %H:%M:%S"))
        automation_state["next_run_iso"] = next_target.isoformat()

        print(f"[*] Next scheduled publishing slot: {automation_state['next_run']} (Sleeping {int(sleep_sec/60)} mins)...")

        # Sleep in 30-second increments so pause/resume responds instantly
        while sleep_sec > 0:
            if automation_state["is_paused"]:
                await asyncio.sleep(10)
                continue
            step = min(30, sleep_sec)
            await asyncio.sleep(step)
            sleep_sec -= step

        if not automation_state["is_paused"]:
            print(f"[{ist_time.now_ist_str()}] Slot time reached! Triggering publication...")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, run_cycle_task)

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(background_cadence_scheduler())

@app.get("/health")
def health():
    tracker = telegram_extractor.load_tracker()
    history = load_recent_history()
    exp = cadence_optimizer.load_experiment_state()
    cfg = config_loader.load_config()
    return {
        "status": "PAUSED" if automation_state["is_paused"] else "RUNNING",
        "service": "Best Find Autonomous Cloud Bot",
        "is_paused": automation_state["is_paused"],
        "is_processing": automation_state["is_processing"],
        "last_run": automation_state["last_run"],
        "next_run": automation_state["next_run"],
        "next_run_iso": automation_state["next_run_iso"],
        "last_message_id": tracker.get("last_processed_id", 0),
        "mongodb_connected": db_mongo.is_connected(),
        "amazon_tag": cfg.get("meta", {}).get("amazon_tag", "tech6s-21"),
        "total_posts": len(history),
        "recent_posts": history[:5],
        "experiment": {
            "phase": exp.get("phase"),
            "current_day": exp.get("current_day", 1),
            "total_days": 7,
            "winning_slot": exp.get("winning_slot_name", "Analyzing..."),
            "status": exp.get("status")
        }
    }

@app.get("/api/status")
def api_status():
    return health()

@app.get("/api/logs")
def api_logs(limit: int = 80):
    return {"logs": log_streamer.get_logs(limit=limit)}

@app.get("/api/experiment")
def api_experiment():
    return cadence_optimizer.load_experiment_state()

@app.post("/api/experiment/refresh")
def api_experiment_refresh():
    cfg = config_loader.load_config()
    res = cadence_optimizer.refresh_experiment_metrics(cfg)
    return {"status": "success", "message": "Experiment metrics refreshed from Meta Graph API", "experiment": res}

@app.post("/pause")
def pause_bot():
    automation_state["is_paused"] = True
    automation_state["status"] = "PAUSED"
    return {"status": "success", "message": "Bot PAUSED. Scheduled runs stopped.", "is_paused": True}

@app.post("/resume")
def resume_bot():
    automation_state["is_paused"] = False
    automation_state["status"] = "RUNNING"
    return {"status": "success", "message": "Bot RESUMED. Cadence active.", "is_paused": False}

@app.post("/trigger-now")
def trigger_now(background_tasks: BackgroundTasks):
    if automation_state["is_processing"]:
        return JSONResponse({"status": "error", "message": "A cycle is already running!"}, status_code=400)
    background_tasks.add_task(run_cycle_task)
    return {"status": "success", "message": "Cycle triggered in background!"}

@app.post("/reset-tracker")
def reset_tracker_endpoint(message_id: int):
    cfg = config_loader.load_config()
    telegram_extractor.reset_tracker_last_id(message_id, cfg)
    return {"status": "success", "message": f"Tracker successfully rewound to Message #{message_id}"}

# ==============================================================
# MASTER DASHBOARD WITH 7-DAY EXPERIMENT & LIVE TERMINAL
# ==============================================================

@app.get("/", response_class=HTMLResponse)
def dashboard():
    tracker = telegram_extractor.load_tracker()
    last_id = tracker.get("last_processed_id", 0)
    history = load_recent_history()
    is_paused = automation_state["is_paused"]
    cfg = config_loader.load_config()
    amazon_tag = cfg.get("meta", {}).get("amazon_tag", "tech6s-21")
    mongo_ok = db_mongo.is_connected()
    exp = cadence_optimizer.load_experiment_state()

    current_day = exp.get("current_day", 1)
    day_pct = int((current_day / 7) * 100)
    is_optimized = exp.get("status") == "OPTIMIZED"
    winning_name = exp.get("winning_slot_name", "Evaluating data...")

    # Slots grid html
    slots_html = ""
    for s_id, s_data in exp.get("slots_performance", {}).items():
        is_winner = exp.get("winning_slot") == s_id
        border_class = "border-emerald-500/80 bg-emerald-950/20" if is_winner else "border-zinc-800 bg-zinc-900/60"
        badge = "<span class='px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-400 font-semibold'>★ WINNING SLOT</span>" if is_winner else ""
        slots_html += f"""
        <div class="p-3 border {border_class} rounded-xl">
            <div class="flex items-center justify-between">
                <span class="text-xs font-semibold text-white">{s_data.get('name')}</span>
                {badge}
            </div>
            <div class="text-sm font-bold text-amber-400 mt-1">{s_data.get('time_ist')} IST</div>
            <div class="flex items-center justify-between text-[11px] text-zinc-400 mt-2">
                <span>Posts: <strong class="text-white">{s_data.get('posts_count', 0)}</strong></span>
                <span>Score: <strong class="text-white">{s_data.get('score', 0.0):.1f}</strong></span>
            </div>
        </div>
        """

    # History feed
    history_html = ""
    if not history:
        history_html = "<div class='text-zinc-500 text-xs py-4'>No posts logged yet. First cycle running shortly.</div>"
    else:
        for item in history:
            ts = ist_time.format_ts_to_ist(item.get("timestamp", ""))
            mid = item.get("telegram_message_id", "")
            summary = item.get("product_name") or item.get("summary", "Home tech product")
            fb_id = item.get("facebook", {}).get("result", "")
            amz_link = item.get("amazon_link", f"https://www.amazon.in/s?k=gadgets&tag={amazon_tag}")
            fb_url = f"https://www.facebook.com/{fb_id}" if fb_id and fb_id != "None" else "#"

            history_html += f"""
            <div class='p-3 bg-zinc-900/80 border border-zinc-800 rounded-lg mb-2 flex flex-col sm:flex-row sm:items-center justify-between text-xs gap-2'>
                <div>
                    <span class='px-2 py-0.5 rounded bg-zinc-800 text-white font-mono text-[11px] mr-2'>Msg #{mid}</span>
                    <span class='text-zinc-200 font-semibold'>{summary}</span>
                    <div class='text-[11px] text-zinc-500 mt-0.5'>{ts}</div>
                </div>
                <div class='flex items-center space-x-2 flex-wrap gap-1'>
                    <a href='{amz_link}' target='_blank' class='px-2.5 py-1 rounded bg-[#FF9900]/20 text-[#FF9900] font-semibold hover:underline flex items-center gap-1'>
                        🛒 Amazon ({amazon_tag})
                    </a>
                    {"<a href='" + fb_url + "' target='_blank' class='px-2.5 py-1 rounded bg-[#0066FF]/20 text-[#0066FF] font-semibold hover:underline flex items-center gap-1'><span>Facebook Video</span> <span>↗</span></a>" if fb_id and fb_id != "None" else "<span class='text-rose-400 font-medium'>● Failed Verification</span>"}
                </div>
            </div>
            """

    toggle_btn = f"""
    <button onclick="toggleBot({'false' if is_paused else 'true'})" class="px-4 py-2 {'bg-emerald-500 hover:bg-emerald-400 text-black' if is_paused else 'bg-red-500 hover:bg-red-400 text-white'} font-semibold text-xs rounded-lg transition-all shadow-sm">
        {'▶️ Resume Bot' if is_paused else '⏸️ Pause Bot'}
    </button>
    """

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Best Find — 7-Day Autonomous Cadence & Cloud Publisher</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
            body {{ font-family: 'Geist', sans-serif; }}
            .terminal-font {{ font-family: 'JetBrains Mono', monospace; }}
            ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
            ::-webkit-scrollbar-track {{ background: #09090b; }}
            ::-webkit-scrollbar-thumb {{ background: #27272a; border-radius: 3px; }}
        </style>
    </head>
    <body class="bg-black text-zinc-100 antialiased min-h-screen p-4 md:p-8">
        <div class="max-w-5xl mx-auto space-y-6">

            <!-- Top Header -->
            <div class="border-b border-zinc-800 pb-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                    <div class="flex items-center space-x-2">
                        <span class="text-white font-bold text-lg tracking-tight">Best Find Automation</span>
                        <span class="text-xs px-2.5 py-0.5 rounded-full border border-amber-500/30 bg-amber-500/10 text-amber-400 font-medium">Original Audio • Direct FB Publisher</span>
                    </div>
                    <p class="text-xs text-zinc-400 mt-1">Telegram @allterapost ➔ Groq AI Captioning ➔ Facebook Page "best find"</p>
                </div>
                <div class="flex items-center space-x-2">
                    <span class="px-2 py-1 rounded text-[11px] font-medium {'bg-emerald-950 text-emerald-400 border border-emerald-800' if mongo_ok else 'bg-zinc-900 text-zinc-400 border border-zinc-800'}">
                        ● MongoDB Atlas {'Connected' if mongo_ok else 'Local'}
                    </span>
                    <span class="px-2 py-1 rounded text-[11px] font-medium bg-zinc-900 text-zinc-300 border border-zinc-800">
                        Page ID: 255894584272909
                    </span>
                </div>
            </div>

            <!-- 7-DAY CADENCE EXPERIMENT HERO CARD -->
            <div class="p-6 bg-gradient-to-br from-zinc-900 to-zinc-950 border border-zinc-800 rounded-2xl shadow-xl space-y-4">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div>
                        <span class="text-xs uppercase font-bold tracking-wider {'text-emerald-400' if is_optimized else 'text-amber-400'}">
                            {'🏆 Autonomous Optimization Complete' if is_optimized else '🔬 7-Day Cadence Experiment in Progress'}
                        </span>
                        <h2 class="text-xl font-bold text-white mt-1">
                            {'Locked Timeframe: ' + winning_name if is_optimized else 'Testing 4 Strategic Daily Slots Across 7 Days'}
                        </h2>
                    </div>
                    <div class="text-right">
                        <span class="text-xs text-zinc-400">Experiment Progress</span>
                        <div class="text-sm font-bold text-white">Day {current_day} of 7 ({day_pct}%)</div>
                    </div>
                </div>

                <!-- Progress Bar -->
                <div class="w-full bg-zinc-800 h-2.5 rounded-full overflow-hidden">
                    <div class="bg-gradient-to-r from-amber-500 to-emerald-500 h-full rounded-full transition-all duration-500" style="width: {day_pct}%"></div>
                </div>

                <!-- Slots Leaderboard Grid -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2">
                    {slots_html}
                </div>
            </div>

            <!-- Countdown & Execution Controls -->
            <div class="p-5 bg-zinc-950 border border-zinc-800 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                    <div class="text-xs text-zinc-500 uppercase font-semibold">Next Scheduled Slot (IST)</div>
                    <div class="text-2xl font-bold text-white mt-1 flex items-center space-x-2">
                        <span id="countdown-timer" class="font-mono text-indigo-400">--:--:--</span>
                        <span class="text-xs text-zinc-500 font-normal">({"Paused" if is_paused else "Target: " + automation_state["next_run"]})</span>
                    </div>
                </div>
                <div class="flex items-center space-x-2 flex-wrap gap-2">
                    {toggle_btn}
                    <button onclick="rewindTracker()" class="px-3.5 py-2 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30 font-semibold text-xs rounded-lg transition-all shadow-sm">
                        🔄 Reprocess Failed
                    </button>
                    <button onclick="triggerNow()" class="px-4 py-2 bg-white hover:bg-zinc-200 text-black font-semibold text-xs rounded-lg transition-all shadow-sm">
                        ⚡ Publish Next Video Now
                    </button>
                </div>
            </div>

            <!-- LIVE TERMINAL CONSOLE -->
            <div class="bg-zinc-950 border border-zinc-800 rounded-xl overflow-hidden shadow-2xl">
                <div class="bg-zinc-900/90 px-4 py-2.5 border-b border-zinc-800 flex items-center justify-between">
                    <div class="flex items-center space-x-2">
                        <span class="w-3 h-3 rounded-full bg-red-500/80 inline-block"></span>
                        <span class="w-3 h-3 rounded-full bg-amber-500/80 inline-block"></span>
                        <span class="w-3 h-3 rounded-full bg-emerald-500/80 inline-block"></span>
                        <span class="text-xs text-zinc-400 font-medium ml-2 terminal-font">bash — best-find-bot.log (IST)</span>
                    </div>
                    <div class="flex items-center space-x-2 text-[11px] text-zinc-500">
                        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                        <span>Live Terminal Stream</span>
                    </div>
                </div>
                <div id="terminal-window" class="p-4 terminal-font text-xs text-emerald-400 bg-black/90 h-64 overflow-y-auto space-y-1">
                    <div class="text-zinc-500">[System] Initializing live terminal stream for Best Find...</div>
                </div>
            </div>

            <!-- Recent Activity Feed -->
            <div class="p-5 bg-zinc-950 border border-zinc-800 rounded-xl space-y-3">
                <div class="text-sm font-semibold text-white flex items-center justify-between">
                    <span>Recent Published Activity & Amazon Links</span>
                    <span class="text-xs text-zinc-500 font-normal">Last 10 cycles</span>
                </div>
                <div>
                    {history_html}
                </div>
            </div>

        </div>

        <script>
            const nextRunTarget = new Date("{automation_state['next_run_iso']}").getTime();
            const isPaused = {'true' if is_paused else 'false'};

            function updateCountdown() {{
                if (isPaused) {{
                    document.getElementById('countdown-timer').innerText = "PAUSED";
                    return;
                }}
                const now = new Date().getTime();
                const distance = nextRunTarget - now;

                if (distance <= 0) {{
                    document.getElementById('countdown-timer').innerText = "STARTING SLOT...";
                    return;
                }}

                const hours = Math.floor(distance / (1000 * 60 * 60));
                const minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
                const seconds = Math.floor((distance % (1000 * 60)) / 1000);

                const pad = (n) => String(n).padStart(2, '0');
                document.getElementById('countdown-timer').innerText = `${{pad(hours)}}h : ${{pad(minutes)}}m : ${{pad(seconds)}}s`;
            }}

            setInterval(updateCountdown, 1000);
            updateCountdown();

            function fetchLogs() {{
                fetch('/api/logs?limit=80')
                    .then(r => r.json())
                    .then(data => {{
                        if (data && data.logs) {{
                            const term = document.getElementById('terminal-window');
                            const wasScrolledBottom = term.scrollHeight - term.clientHeight <= term.scrollTop + 50;
                            term.innerHTML = data.logs.map(line => {{
                                let colorClass = 'text-zinc-300';
                                if (line.includes('[+]') || line.includes('[📊]')) colorClass = 'text-emerald-400';
                                else if (line.includes('[*]') || line.includes('[🧹]')) colorClass = 'text-cyan-400';
                                else if (line.includes('[!]')) colorClass = 'text-amber-400';
                                else if (line.includes('[X]') || line.includes('Error')) colorClass = 'text-rose-400';
                                return `<div class="${{colorClass}}">${{line}}</div>`;
                            }}).join('') + '<div class="text-zinc-600 animate-pulse">_</div>';
                            if (wasScrolledBottom) term.scrollTop = term.scrollHeight;
                        }}
                    }})
                    .catch(e => {{}});
            }}

            setInterval(fetchLogs, 2500);
            fetchLogs();

            function triggerNow() {{
                if (confirm("Publish the next video to Facebook Page 'best find' right now?")) {{
                    fetch('/trigger-now', {{ method: 'POST' }})
                        .then(r => r.json())
                        .then(data => {{
                            alert(data.message);
                            fetchLogs();
                        }})
                        .catch(e => alert("Error: " + e));
                }}
            }}

            function rewindTracker() {{
                const targetId = prompt("Enter the Telegram Message ID to rewind to:", "28824");
                if (targetId && !isNaN(targetId)) {{
                    fetch('/reset-tracker?message_id=' + targetId, {{ method: 'POST' }})
                        .then(r => r.json())
                        .then(data => {{
                            alert(data.message);
                            fetchLogs();
                            location.reload();
                        }})
                        .catch(e => alert("Error: " + e));
                }}
            }}

            function toggleBot(pause) {{
                const endpoint = pause ? '/pause' : '/resume';
                const actionName = pause ? 'PAUSE' : 'RESUME';
                if (confirm("Are you sure you want to " + actionName + " the automated publisher?")) {{
                    fetch(endpoint, {{ method: 'POST' }})
                        .then(r => r.json())
                        .then(data => {{
                            alert(data.message);
                            location.reload();
                        }})
                        .catch(e => alert("Error: " + e));
                }}
            }}
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
