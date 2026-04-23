from fastapi import FastAPI, Request, BackgroundTasks
import requests
import os

app = FastAPI()

TELNYX_API_KEY = os.getenv("TELNYX_API_KEY")
TELNYX_FROM_NUMBER = os.getenv("TELNYX_FROM_NUMBER")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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
    requests.post(url, headers=headers, json=data)

def generate_reply(msg):
    prompt = f"""
You are a real estate SMS assistant qualifying buyers for vacant land deals in Cripple Creek.

User said: {msg}

Reply naturally, short, and ask ONE question.
Focus on:
- area
- land type
- budget
- timeline
"""

    res = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": prompt}]
        }
    )

    return res.json()["choices"][0]["message"]["content"]

def process(payload):
    data = payload.get("data", {})
    if data.get("event_type") != "message.received":
        return

    text = data["payload"]["text"]
    phone = data["payload"]["from"]["phone_number"]

    reply = generate_reply(text)
    send_sms(phone, reply)

@app.post("/webhooks/telnyx/sms")
async def webhook(request: Request, bg: BackgroundTasks):
    payload = await request.json()
    bg.add_task(process, payload)
    return {"ok": True}
