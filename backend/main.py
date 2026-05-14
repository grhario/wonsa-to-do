from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
import os
from dotenv import load_dotenv
from datetime import date, datetime, timedelta
from typing import Optional, List
import json
import threading
import time

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
    category: str
    role: str
    priority: str
    task_type: str = 'wajib'
    description: Optional[str] = None
    due_date: Optional[date] = None
    link: Optional[str] = None
    attachment: Optional[dict] = None

class ClosingLog(BaseModel):
    officer_name: str
    log_date: Optional[date] = None
    notes: Optional[str] = None

@app.get("/")
def root():
    return {"status": "ok", "app": "Eiger Task Manager"}

@app.get("/tasks")
def get_tasks(category: Optional[str] = None, role: Optional[str] = None):
    query = supabase.table("tasks").select("*").order("created_at", desc=False)
    if category:
        query = query.eq("category", category)
    if role and role != "Semua":
        query = query.in_("role", [role, "Semua"])
    result = query.execute()
    return result.data

@app.post("/tasks")
def create_task(task: Task):
    data = task.dict()
    data.pop("attachment", None)
    if data.get("due_date"):
        data["due_date"] = str(data["due_date"])
    result = supabase.table("tasks").insert(data).execute()
    new_task = result.data[0]
    try:
        service = get_calendar_service()
        if service:
            calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
            now = datetime.utcnow()
            event_date = str(new_task.get("due_date") or now.strftime("%Y-%m-%d"))
            event = {
                "summary": f"⏰ REMINDER: {new_task['title']}",
                "description": f"Task baru!\nKategori: {new_task['category']}\nPrioritas: {new_task['priority']}",
                "start": {"dateTime": event_date + "T08:00:00+07:00", "timeZone": "Asia/Jakarta"},
                "end": {"dateTime": event_date + "T08:30:00+07:00", "timeZone": "Asia/Jakarta"},
                "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}, {"method": "popup", "minutes": 30}]}
            }
            service.events().insert(calendarId=calendar_id, body=event).execute()
    except:
        pass
    return new_task

@app.patch("/tasks/{task_id}")
def update_task_status(task_id: str, is_done: Optional[bool] = None):
    data = {}
    if is_done is not None:
        data["is_done"] = is_done
    result = supabase.table("tasks").update(data).eq("id", task_id).execute()
    return result.data[0]

@app.put("/tasks/{task_id}")
def update_task_full(task_id: str, task: Task):
    try:
        check = supabase.table("tasks").select("id").eq("id", task_id).execute()
        if not check.data:
            raise HTTPException(status_code=404, detail="Task tidak ditemukan")
        
        data = task.dict()
        data.pop("attachment", None)
        if data.get("due_date"):
            data["due_date"] = str(data["due_date"])
        
        result = supabase.table("tasks").update(data).eq("id", task_id).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Gagal mengupdate task")
        
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error mengupdate task: {str(e)}")

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    try:
        # Check if task exists first
        check = supabase.table("tasks").select("id").eq("id", task_id).execute()
        if not check.data:
            raise HTTPException(status_code=404, detail="Task tidak ditemukan")
        
        result = supabase.table("tasks").delete().eq("id", task_id).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Gagal menghapus task dari database")
        
        return {"message": "Task berhasil dihapus", "deleted_id": task_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error menghapus task: {str(e)}")

@app.post("/tasks/{task_id}/toggle")
def toggle_task(task_id: str):
    current = supabase.table("tasks").select("is_done").eq("id", task_id).execute()
    if not current.data:
        raise HTTPException(status_code=404, detail="Task tidak ditemukan")
    new_status = not current.data[0]["is_done"]
    result = supabase.table("tasks").update({"is_done": new_status}).eq("id", task_id).execute()
    return result.data[0]

@app.get("/closing/today")
def get_today_tasks():
    wajib = supabase.table("tasks").select("*").eq("task_type", "wajib").execute()
    insidental = supabase.table("tasks").select("*").eq("task_type", "insidental").execute()
    return {"wajib": wajib.data, "insidental": insidental.data}

@app.post("/closing/submit")
def submit_closing(log: ClosingLog):
    pending = supabase.table("tasks").select("*").eq("is_done", False).execute()
    done = supabase.table("tasks").select("*").eq("is_done", True).execute()
    total = len(pending.data) + len(done.data)
    msg = f"📋 *LAPORAN CLOSING HARIAN*\n👤 Petugas: {log.officer_name}\n✅ Selesai: {len(done.data)}/{total} task"
    if log.notes:
        msg += f"\n📝 Catatan: {log.notes}"
    log_data = {
        "officer_name": log.officer_name,
        "log_date": str(log.log_date or date.today()),
        "total_tasks": total,
        "done_tasks": len(done.data),
        "notes": log.notes
    }
    supabase.table("closing_logs").insert(log_data).execute()
    return {"success": True, "wa_message": msg}

@app.get("/settings")
def get_settings():
    result = supabase.table("settings").select("*").execute()
    return {row["key"]: row["value"] for row in result.data}

def get_calendar_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds_json = os.getenv("GOOGLE_CREDENTIALS")
        if not creds_json:
            return None
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/calendar"]
        )
        return build("calendar", "v3", credentials=creds)
    except:
        return None

@app.get("/calendar/remind-pending")
def remind_pending_tasks():
    service = get_calendar_service()
    if not service:
        raise HTTPException(status_code=500, detail="Google Calendar tidak terkonfigurasi")
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    tasks = supabase.table("tasks").select("*").eq("is_done", False).execute()
    created = []
    for task in tasks.data:
        now = datetime.utcnow()
        event_date = str(task.get("due_date") or now.strftime("%Y-%m-%d"))
        event = {
            "summary": f"⏰ REMINDER: {task['title']}",
            "description": f"Task belum selesai!\nKategori: {task['category']}\nPrioritas: {task['priority']}",
            "start": {"dateTime": event_date + "T08:00:00+07:00", "timeZone": "Asia/Jakarta"},
            "end": {"dateTime": event_date + "T08:30:00+07:00", "timeZone": "Asia/Jakarta"},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}, {"method": "popup", "minutes": 30}]}
        }
        result = service.events().insert(calendarId=calendar_id, body=event).execute()
        created.append(result.get("id"))
    return {"success": True, "events_created": len(created)}

def daily_calendar_sync():
    while True:
        now = datetime.utcnow()
        if now.hour == 1 and now.minute == 0:
            try:
                remind_pending_tasks()
            except:
                pass
        time.sleep(60)

threading.Thread(target=daily_calendar_sync, daemon=True).start()