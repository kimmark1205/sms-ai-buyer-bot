from fastapi import FastAPI, Request, BackgroundTasks
import requests
import os
import sqlite3
from datetime import datetime

app = FastAPI()

TELNYX_API_KEY = os.getenv("TELNYX_API_KEY")
TELNYX_FROM_NUMBER = os.getenv("TELNYX_FROM_NUMBER")
ZAPIER_WEBHOOK_URL = os.getenv("ZAPIER_WEBHOOK_URL")

DB_FILE = "sms_conversations.db"
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            role TEXT,
            message TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS opt_outs (
            phone TEXT PRIMARY KEY,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


def save_message(phone, role, message):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (phone, role, message, created_at) VALUES (?, ?, ?, ?)",
        (phone, role, message, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def get_history(phone, limit=10):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "SELECT role, message FROM messages WHERE phone = ? ORDER BY id DESC LIMIT ?",
        (phone, limit)
    )
    rows = cur.fetchall()
    conn.close()

    rows.reverse()
    return [{"role": role, "content": msg} for role, msg in rows]


def opt_out(phone):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO opt_outs (phone, created_at) VALUES (?, ?)",
        (phone, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def is_opted_out(phone):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT phone FROM opt_outs WHERE phone = ?", (phone,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def send_sms(to, text):
    url = "https://api.telnyx.com/v2/messages"

    headers = {
        "Authorization": f"Bearer {TELNYX_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "from": TELNYX_FROM_NUMBER,
        "to": to,
        "text": text
    }

    response = requests.post(url, headers=headers, json=data, timeout=30)
    response.raise_for_status()
    return response.json()


def classify_interest(text):
    t = text.lower()

    if any(word in t for word in ["yes", "yeah", "yep", "interested", "send", "buying", "still buying", "what do you have"]):
        return "hot"

    if any(word in t for word in ["maybe", "depends", "later", "possibly", "what price"]):
        return "warm"

    if any(word in t for word in ["no", "not interested", "stop", "remove", "wrong number"]):
        return "dead"

    return "unknown"


def generate_ai_reply(phone, incoming_text):
    text = incoming_text.lower().strip()
    history = get_history(phone)
    user_count = len([m for m in history if m["role"] == "user"])

    if any(word in text for word in ["stop", "unsubscribe", "remove", "cancel", "quit"]):
        return ""

    if any(word in text for word in ["no", "not interested", "wrong number"]):
        return "Got it, appreciate you letting me know 👍"

    if "@" in text:
        return "Perfect, I’ll send you deals that fit what you’re looking for 👍"

    if any(word in text for word in ["yes", "yeah", "yep", "still buying", "interested", "send"]):
        return "Nice, are you mainly focused on Cripple Creek or open to nearby areas too?"

    if any(word in text for word in ["cripple", "nearby", "colorado", "co"]):
        return "Makes sense. What price range are you usually targeting?"

    if any(word in text for word in ["cash", "financing", "loan", "hard money"]):
        return "Solid. How soon are you looking to pick up your next deal?"

    if any(char.isdigit() for char in text):
        return "Gotcha. Are you buying cash or using financing?"

    if user_count == 1:
        return "Got it. What areas are you buying in right now?"

    if user_count == 2:
        return "Makes sense. What price range are you usually targeting?"

    if user_count == 3:
        return "Gotcha. Are you buying cash or using financing?"

    if user_count == 4:
        return "Solid. How soon are you looking to pick up your next deal?"

    if user_count == 5:
        return "Sounds good. What’s the best email to send deals to?"

    return "Got it, I’ll keep that in mind 👍"


def send_to_zapier(phone, incoming_text, reply, interest):
    if not ZAPIER_WEBHOOK_URL:
        return

    payload = {
        "phone": phone,
        "latest_reply": incoming_text,
        "ai_reply": reply,
        "interest_level": interest,
        "lead_type": "vacant_land_buyer",
        "market": "Cripple Creek",
        "created_at": datetime.utcnow().isoformat()
    }

    try:
        requests.post(ZAPIER_WEBHOOK_URL, json=payload, timeout=20)
    except Exception as e:
        print("Zapier error:", str(e))


def process_inbound(payload):
    data = payload.get("data", {})
    event_type = data.get("event_type")

    if event_type != "message.received":
        return

    payload_data = data.get("payload", {})

    text = (payload_data.get("text") or "").strip()
    from_data = payload_data.get("from", {})
    phone = from_data.get("phone_number")

    if not text or not phone:
        return

    if text.upper().strip() in STOP_WORDS:
        opt_out(phone)
        save_message(phone, "user", text)
        send_to_zapier(phone, text, "", "dead")
        return

    if is_opted_out(phone):
        return

    save_message(phone, "user", text)

    interest = classify_interest(text)
    reply = generate_ai_reply(phone, text)

    if reply:
        save_message(phone, "assistant", reply)
        send_sms(phone, reply)

    send_to_zapier(phone, text, reply, interest)

    print({
        "phone": phone,
        "incoming": text,
        "reply": reply,
        "interest": interest
    })


@app.get("/")
async def health_check():
    return {
        "ok": True,
        "message": "SMS AI buyer bot is running"
    }


@app.post("/webhooks/telnyx/sms")
async def telnyx_webhook(request: Request, bg: BackgroundTasks):
    payload = await request.json()
    bg.add_task(process_inbound, payload)
    return {"ok": True}


@app.post("/send-sms")
async def send_outbound(request: Request):
    data = await request.json()

    to = data.get("to")
    text = data.get("text")

    if not to or not text:
        return {
            "ok": False,
            "error": "Missing 'to' or 'text'"
        }

    result = send_sms(to, text)
    save_message(to, "assistant", text)

    return {
        "ok": True,
        "status": "sent",
        "result": result
    }
