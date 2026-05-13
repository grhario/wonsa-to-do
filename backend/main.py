from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
import os
from dotenv import load_dotenv
from datetime import date
from typing import Optional

load_dotenv()

app = FastAPI(title="Eiger Task Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    return result.data[0]

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