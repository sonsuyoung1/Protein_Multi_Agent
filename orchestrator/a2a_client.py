# orchestrator/a2a_client.py
# ============================================================
# A2A 클라이언트
# ============================================================
import requests
import uuid
from config import KG_AGENT_URL, REPORT_AGENT_URL, KAGGLE_API_URL, KAGGLE_API_KEY, A2A_TIMEOUT


def send_a2a_task(agent_url: str, message: str, sender: str = "orchestrator") -> dict:
    task_id = str(uuid.uuid4())
    payload = {"task_id": task_id, "sender": sender, "message": message}
    try:
        res = requests.post(
            f"{agent_url}/task",
            json=payload,
            timeout=A2A_TIMEOUT
        )
        return res.json()
    except Exception as e:
        return {"task_id": task_id, "status": "failed", "result": {"error": str(e)}}


def get_agent_card(agent_url: str) -> dict:
    try:
        res = requests.get(f"{agent_url}/.well-known/agent-card", timeout=10)
        return res.json()
    except Exception as e:
        return {"error": str(e)}


def analyze_image_kaggle(image_path: str) -> dict:
    try:
        res = requests.post(
            f"{KAGGLE_API_URL}/analyze",
            params={"image_path": image_path},
            headers={"X-API-Key": KAGGLE_API_KEY},
            timeout=300
        )
        return res.json()
    except Exception as e:
        return {"error": str(e)}