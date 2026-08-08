# config.py
# ============================================================
# 공통 설정
# ============================================================
import os
from pathlib import Path
from dotenv import load_dotenv


def _ensure_ssl_cert_file():
    if os.environ.get("SSL_CERT_FILE"):
        return

    try:
        import certifi

        cert_path = certifi.where()
        if cert_path and os.path.exists(cert_path):
            os.environ["SSL_CERT_FILE"] = cert_path
            return
    except Exception:
        pass

    conda_candidates = [
        Path(os.sys.prefix) / "Library" / "ssl" / "cacert.pem",
        Path(os.sys.prefix).parent.parent / "Library" / "ssl" / "cacert.pem",
    ]
    for cert_path in conda_candidates:
        if cert_path.exists():
            os.environ["SSL_CERT_FILE"] = str(cert_path)
            return


_ensure_ssl_cert_file()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

NEO4J_MCP_PATH = os.path.join(BASE_DIR, "mcp", "neo4j_mcp_server.py")

# Neo4j AuraDB - .env에서 로드 (과거 하드코딩된 값은 노출되어 더 이상 사용하지 않음)
NEO4J_URI = os.environ.get("NEO4J_URI", "")
_NEO4J_USER = os.environ.get("NEO4J_USER", "")
_NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_AUTH = (_NEO4J_USER, _NEO4J_PASSWORD)

if not NEO4J_URI or not _NEO4J_USER or not _NEO4J_PASSWORD:
    print(
        "[config] 경고: NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD가 .env에 설정되어 있지 않습니다. "
        "Neo4j에 접근하는 기능(KG/Report Agent 등)은 동작하지 않습니다."
    )

# Ollama
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

# Kaggle Image Agent (ngrok URL + 공유 비밀키 인증)
KAGGLE_API_URL = os.environ.get("KAGGLE_API_URL", "")
KAGGLE_API_KEY = os.environ.get("KAGGLE_API_KEY", "")

# Agent 서버 포트
KG_AGENT_URL     = os.environ.get("KG_AGENT_URL", "http://localhost:8001")
REPORT_AGENT_URL = os.environ.get("REPORT_AGENT_URL", "http://localhost:8002")

# A2A 설정
A2A_TIMEOUT = int(os.environ.get("A2A_TIMEOUT", "60"))
