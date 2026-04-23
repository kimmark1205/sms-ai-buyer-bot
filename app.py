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

    response = requests.post(url, headers=headers, json=data, timeout=30)
    response.raise_for_status()
    return response.json()


def generate_reply(msg):
    prompt = f"""
You are a real estate SMS assistant qualifying buyers for vacant land deals in Cripple Creek.

Your job is to continue the conversation naturally after a buyer replies to our outbound text.

Rules:
- Keep replies short and natural
- Ask only ONE question at a time
- Focus on:
  1. area
  2. land type
  3. budget
  4. cash or financing
  5. timeline
- Do not sound robotic
- Do not over-explain
- If they are not interested, respond politely and end
- If they ask to stop, acknowledge and end

User said: {msg}
"""

    res = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-4.1-mini",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.4
        },
        timeout=60
    )

    res.raise_for_status()
    data = res.json()
    return data["choices"][0]["message"]["content"].strip()


def process_inbound(payload):
    data = payload.get("data", {})
    event_type = data.get("event_type")

    if event_type != "message.received":
        return

    payload_data = data.get("payload", {})
    text = payload_data.get("text", "").strip()
    from_data = payload_data.get("from", {})
    phone = from_data.get("phone_number")

    if not text or not phone:
        return

    stop_words = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
    if text.upper().strip() in stop_words:
        return

    reply = generate_reply(text)
    send_sms(phone, reply)


@app.get("/")
async def health_check():
    return {"ok": True, "message": "SMS AI buyer bot is running"}


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

    return {
        "ok": True,
        "status": "sent",
        "result": result
    }
