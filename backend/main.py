from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
import os
from dotenv import load_dotenv
from datetime import date, datetime, timedelta
from typing import Optional

load_dotenv()

app = FastAPI(title="Eiger Task Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
   allow_headers=["*"],
)

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

class Task(BaseModel):
    title: str
    category: str        # Harian / Mingguan / Bulanan
    role: str            # SPV / Retail Assistant / Semua
    priority: str        # Tinggi / Sedang / Rendah
    task_type: str = 'wajib'   # wajib / insidental
    description: Optional[str] = None
    due_date: Optional[date] = None
    link: Optional[str] = None
    attachment: Optional[dict] = None

# GET semua task
@app.get("/tasks")
def get_tasks(category: Optional[str] = None, role: Optional[str] = None):
    query = supabase.table("tasks").select("*").order("created_at", desc=False)
    if category:
        query = query.eq("category", category)
    if role and role != "Semua":
        query = query.in_("role", [role, "Semua"])
    result = query.execute()
    return result.data

# POST buat task baru
@app.post("/tasks")
def create_task(task: Task):
    result = supabase.table("tasks").insert(task.dict()).execute()
    new_task = result.data[0]
    
    # Auto sync ke Google Calendar
    try:
        service = get_calendar_service()
        if service:
            calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
            now = datetime.utcnow()
            event = {
                "summary": f"⏰ REMINDER: {new_task['title']}",
                "description": f"Task baru!\nKategori: {new_task['category']}\nPrioritas: {new_task['priority']}",
                "start": {"dateTime": (new_task['due_date'] or now.strftime("%Y-%m-%d")) + "T08:00:00+07:00", "timeZone": "Asia/Jakarta"},
"end": {"dateTime": (new_task['due_date'] or now.strftime("%Y-%m-%d")) + "T08:30:00+07:00", "timeZone": "Asia/Jakarta"},
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 0},
                        {"method": "popup", "minutes": 30}
                    ]
                }
            }
            service.events().insert(calendarId=calendar_id, body=event).execute()
    except:
        pass  # Jangan gagalkan task creation kalau Calendar error
    
    return new_task

# PATCH update status done
@app.patch("/tasks/{task_id}/done")
def toggle_done(task_id: str, is_done: bool):
    result = supabase.table("tasks").update({"is_done": is_done}).eq("id", task_id).execute()
    return result.data[0]

# DELETE hapus task
@app.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    supabase.table("tasks").delete().eq("id", task_id).execute()
    return {"message": "Task dihapus"}

# GET reminder WA — task prioritas tinggi yang belum selesai
@app.get("/reminder/whatsapp")
def get_wa_reminder():
    tasks = supabase.table("tasks").select("*").eq("is_done", False).eq("priority", "Tinggi").execute()
    task_list = "\n".join([f"• {t['title']}" for t in tasks.data])
    message = f"🔔 Reminder Task Prioritas Tinggi\nEiger Wonosari\n\n{task_list}"
    wa_url = f"https://wa.me/?text={message}"
    return {"url": wa_url, "count": len(tasks.data)}

# ─── GOOGLE CALENDAR INTEGRATION ─────────────────────────
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

def get_calendar_service():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if not creds_json:
        return None
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds)

@app.post("/calendar/sync")
def sync_tasks_to_calendar():
    service = get_calendar_service()
    if not service:
        raise HTTPException(status_code=500, detail="Google Calendar tidak terkonfigurasi")
    
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    tasks = supabase.table("tasks").select("*").eq("is_done", False).execute()
    
    created = []
    for task in tasks.data:
        now = datetime.utcnow()
        event = {
            "summary": f"⏰ REMINDER: {task['title']}",
            "description": f"Task belum selesai!\nKategori: {task['category']}\nPrioritas: {task['priority']}",
            "start": {"dateTime": now.strftime("%Y-%m-%dT08:00:00+07:00"), "timeZone": "Asia/Jakarta"},
            "end": {"dateTime": now.strftime("%Y-%m-%dT08:30:00+07:00"), "timeZone": "Asia/Jakarta"},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 0},
                    {"method": "popup", "minutes": 30}
                ]
            }
        }
        result = service.events().insert(calendarId=calendar_id, body=event).execute()
        created.append(result.get("id"))
    
    return {"success": True, "events_created": len(created)}

@app.get("/calendar/remind-pending")
def remind_pending_tasks():
    return sync_tasks_to_calendar()

# ─── CRON JOB HARIAN ─────────────────────────────────────
import threading
import time

def daily_calendar_sync():
    while True:
        now = datetime.utcnow()
        # Sync tiap hari jam 01:00 UTC = 08:00 WIB
        if now.hour == 1 and now.minute == 0:
            try:
                remind_pending_tasks()
            except:
                pass
        time.sleep(60)  # cek tiap menit

# Jalankan cron job di background
threading.Thread(target=daily_calendar_sync, daemon=True).start()
