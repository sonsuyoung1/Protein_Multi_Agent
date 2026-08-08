# agents/kg_agent/server.py
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn, uuid, sys, os, asyncio, re, json
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Any

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import NEO4J_MCP_PATH, A2A_TIMEOUT

mcp_client = None
neo4j_tools_by_name = {}


# 질문에서 단일 날짜를 찾아 YYYY-MM-DD 형식으로 변환합니다.
def _extract_date(question: str) -> str | None:
    match = re.search(r"(\d{4})[-년./\s]*(\d{1,2})[-월./\s]*(\d{1,2})", question)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    match = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", question)
    if match:
        month, day = match.groups()
        return f"{datetime.now().year:04d}-{int(month):02d}-{int(day):02d}"

    return None


# 질문에서 시작일과 종료일을 찾아 날짜 범위로 변환합니다.
def _extract_date_range(question: str) -> tuple[str, str] | None:
    patterns = [
        r"(\d{4})[-년./\s]*(\d{1,2})[-월./\s]*(\d{1,2}).*?(?:부터|~|까지).*?(\d{4})[-년./\s]*(\d{1,2})[-월./\s]*(\d{1,2})",
        r"(\d{1,2})\s*월\s*(\d{1,2})\s*일.*?(?:부터|~|까지).*?(\d{1,2})\s*월\s*(\d{1,2})\s*일",
    ]
    for idx, pattern in enumerate(patterns):
        match = re.search(pattern, question)
        if not match:
            continue
        values = match.groups()
        if idx == 0:
            y1, m1, d1, y2, m2, d2 = values
            return f"{int(y1):04d}-{int(m1):02d}-{int(d1):02d}", f"{int(y2):04d}-{int(m2):02d}-{int(d2):02d}"
        m1, d1, m2, d2 = values
        year = datetime.now().year
        return f"{year:04d}-{int(m1):02d}-{int(d1):02d}", f"{year:04d}-{int(m2):02d}-{int(d2):02d}"
    return None


# 그래프 관계 탐색 질문에서 ChangeEvent 노드 ID를 추출합니다.
def _extract_node_id(question: str) -> str | None:
    match = re.search(r"(ChangeEvent_[A-Za-z0-9_\-.]+)", question)
    return match.group(1).rstrip("., )]") if match else None


# MCP tool 실행 결과를 문자열로 통일합니다.
def _stringify_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        texts = []
        for item in result:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text", "")))
            else:
                texts.append(str(item))
        return "\n".join(text for text in texts if text)
    if hasattr(result, "content"):
        return str(result.content)
    return str(result)


# MCP tool이 반환한 JSON 문자열을 Python 객체로 변환합니다.
def _parse_json_result(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


# Neo4j Record 하나를 사용자가 읽기 쉬운 텍스트로 정리합니다.
def _format_record(record: dict) -> str:
    image = record.get("target_image") or record.get("filename") or record.get("image") or "unknown"
    date = record.get("date") or record.get("timestamp") or "unknown"
    total_change = record.get("total_change", record.get("total", "N/A"))
    change = record.get("change", "N/A")
    strong_change = record.get("strong_change", "N/A")
    speed = record.get("daily_change_speed_per_day", "N/A")
    return (
        f"- 이미지: {image}\n"
        f"  날짜: {date}\n"
        f"  전체 변화율(total_change): {total_change}%\n"
        f"  change: {change}% / strong_change: {strong_change}%\n"
        f"  변화 속도: {speed}%p/day"
    )


# 질문 키워드를 기준으로 호출할 MCP tool을 선택.
def select_mcp_tool(question: str) -> tuple[str, dict, str] | None:
    date = _extract_date(question)
    if date:
        return "search_by_date", {"date": date}, "날짜 조회"

    if any(word in question for word in ["변화 속도", "변화속도", "속도", "빠른", "느린"]):
        order = "asc" if any(word in question for word in ["느린", "가장 낮", "가장 작", "최소"]) else "desc"
        return "search_by_speed", {"order": order, "limit": 5}, "변화 속도 조회"
    
    if any(word in question for word in ["가장 높은", "최고", "최대", "제일 높은", "많이", "상위"]):
        return "search_top_change", {"order": "desc", "limit": 5}, "변화율 상위 조회"

    if any(word in question for word in ["전체 통계", "통계", "전체 요약", "요약"]):
        return "get_statistics", {}, "전체 통계 조회"

    date_range = _extract_date_range(question)
    if date_range:
        start_date, end_date = date_range
        return "search_by_date_range", {"start_date": start_date, "end_date": end_date}, "기간 조회"

    if any(word in question for word in ["가장 낮은", "최저", "최소", "제일 낮은", "적은", "하위"]):
        return "search_top_change", {"order": "asc", "limit": 5}, "변화율 하위 조회"

    
    
    if any(word in question for word in ["관계", "그래프", "연결", "노드"]):
        node_id = _extract_node_id(question)
        if node_id:
            return "explore_graph", {"node_id": node_id}, "그래프 관계 탐색"

    return None


# MCP tool 원시 결과를 최종 한국어 답변 형식으로 가공합니다.
def build_answer(tool_name: str, args: dict, raw_text: str) -> str:
    parsed = _parse_json_result(raw_text)

    if tool_name in {"search_by_date", "search_by_date_range", "search_top_change", "search_by_speed"}:
        if not isinstance(parsed, list):
            return raw_text
        if not parsed:
            return "조건에 맞는 KG record를 찾지 못했습니다."

        records_text = "\n".join(_format_record(record) for record in parsed[:5])
        
        if tool_name == "search_by_speed":
            label = "가장 느린" if args.get("order") == "asc" else "가장 빠른"
            top = parsed[0]
            image = top.get("target_image") or top.get("filename") or "unknown"
            speed = top.get("daily_change_speed_per_day", "N/A")
            return f"변화 속도가 {label} 이미지는 {image}입니다. 변화 속도는 {speed}%p/day입니다.\n\n{records_text}"

        if tool_name == "search_top_change":
            label = "낮은" if args.get("order") == "asc" else "높은"
            return f"전체 변화율(total_change)이 {label} 순서의 KG record입니다.\n\n{records_text}"

        if tool_name == "search_by_date_range":
            return f"{args['start_date']}부터 {args['end_date']}까지의 KG 조회 결과입니다.\n\n{records_text}"

        return f"{args['date']} 기준 KG 조회 결과입니다.\n\n{records_text}"

    if tool_name == "get_statistics":
        if isinstance(parsed, dict):
            lines = [f"- {key}: {value}" for key, value in parsed.items()]
            return "전체 KG 통계입니다.\n\n" + "\n".join(lines)
        return raw_text

    if tool_name == "explore_graph":
        if isinstance(parsed, list):
            return f"{args['node_id']} 노드의 연결 관계입니다.\n\n" + json.dumps(parsed, ensure_ascii=False, indent=2)
        return raw_text

    return raw_text


# 선택된 MCP tool을 호출.
async def call_mcp_tool(tool_name: str, args: dict) -> str:
    tool = neo4j_tools_by_name.get(tool_name)
    if tool is None:
        available = ", ".join(sorted(neo4j_tools_by_name))
        raise RuntimeError(f"MCP tool을 찾지 못했습니다: {tool_name}. available={available}")

    result = await asyncio.wait_for(tool.ainvoke(args), timeout=A2A_TIMEOUT)
    return _stringify_tool_result(result)


# FastAPI 서버 시작/종료 시 MCP 클라이언트와 Neo4j tool 목록을 관리합니다.
@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_client, neo4j_tools_by_name

    from langchain_mcp_adapters.tools import load_mcp_tools
    from fastmcp import Client

    mcp_client = Client(NEO4J_MCP_PATH)
    await mcp_client.__aenter__()
    neo4j_tools = await load_mcp_tools(mcp_client.session)
    neo4j_tools_by_name = {tool.name: tool for tool in neo4j_tools}
    print("[KG Agent] Neo4j tools:", sorted(neo4j_tools_by_name), flush=True)
    print("KG Agent initialized without Deep Agent", flush=True)

    try:
        yield
    finally:
        if mcp_client is not None:
            await mcp_client.__aexit__(None, None, None)
        print("KG Agent 종료", flush=True)


app = FastAPI(title="KG Agent", lifespan=lifespan)


class A2ATask(BaseModel):
    task_id: str = ""
    sender: str = ""
    message: str = ""


# A2A 클라이언트가 KG Agent의 기본 정보를 확인하는 엔드포인트입니다.
@app.get("/.well-known/agent-card")
async def agent_card():
    return {
        "name": "KG Agent",
        "description": "단백질 합성 KG 검색 (rule-based MCP tool caller)",
        "version": "1.0",
        "url": "http://localhost:8001",
        "capabilities": ["kg_search", "date_query", "trend_analysis", "mcp_tool_calling"],
    }


# Supervisor가 보낸 질문을 받아 MCP tool 호출 결과로 답변합니다.
@app.post("/task")
async def handle_task(task: A2ATask):
    task_id = task.task_id or str(uuid.uuid4())
    print("[KG Agent] /task 요청 받음:", task.message, flush=True)

    try:
        selected = select_mcp_tool(task.message)
        if selected is None:
            answer = (
                "현재 KG Agent는 안정적인 MCP tool 직접 호출 방식으로 동작합니다. "
                "날짜 조회, 변화율 상위/하위, 변화 속도 질문 중 하나로 물어봐 주세요."
            )
            return {"task_id": task_id, "status": "completed", "result": {"answer": answer}}

        tool_name, args, reason = selected
        print("=" * 60, flush=True)
        print(f"[KG Agent] MCP tool 선택: {tool_name}", flush=True)
        print(f"[KG Agent] 선택 이유: {reason}", flush=True)
        print(f"[KG Agent] tool args: {args}", flush=True)
        print("=" * 60, flush=True)

        raw_text = await call_mcp_tool(tool_name, args)
        print(f"[KG Agent] MCP tool 완료: {tool_name}", flush=True)

        answer = build_answer(tool_name, args, raw_text)
        print("[KG Agent] 답변 생성 완료", flush=True)

        return {"task_id": task_id, "status": "completed", "result": {"answer": answer}}

    except Exception as e:
        print("[KG Agent] 오류 발생:", repr(e), type(e).__name__, flush=True)
        return {"task_id": task_id, "status": "failed", "result": {"error": str(e) or type(e).__name__}}


# 서버가 살아 있는지 확인하는 health check 엔드포인트입니다.
@app.get("/health")
async def health():
    return {"status": "ok", "agent": "KG Agent", "time": str(datetime.now())}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
