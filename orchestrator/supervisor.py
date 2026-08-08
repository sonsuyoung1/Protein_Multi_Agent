# orchestrator/supervisor.py
# ============================================================
# Supervisor - Ollama 기반 LLM 라우팅
# ============================================================
import re
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OLLAMA_URL, OLLAMA_MODEL

import requests


def route(question: str, has_image: bool = False) -> str:
    """IMAGE_AGENT / KG_AGENT / REPORT_AGENT 라우팅"""

    if has_image:
        return "IMAGE_AGENT"

    prompt = f"""You are a routing agent. Choose ONE agent.

Question: {question}

Rules:
- Specific date/image/change rate/speed questions → KG_AGENT
- Summary/report/trend/statistics → REPORT_AGENT

Output ONLY JSON:
{{"route": "KG_AGENT"}} or {{"route": "REPORT_AGENT"}}"""

    try:
        res = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 30, "temperature": 0}
            },
            timeout=30
        )
        generated = res.json()["response"].strip()
        print(f"[Supervisor LLM] {generated}")

        match = re.search(r'\{"route":\s*"(KG_AGENT|REPORT_AGENT)"\}', generated)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"[Supervisor] LLM 오류: {e}")

    # 안전장치
    if any(w in question for w in ["보고서", "요약", "추세", "통계", "전체"]):
        return "REPORT_AGENT"
    return "KG_AGENT"
