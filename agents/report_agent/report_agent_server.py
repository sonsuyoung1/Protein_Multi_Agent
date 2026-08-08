# agents/report_agent/server.py
# ============================================================
# Report Agent - rule-based MCP tool caller + FastAPI A2A server
# MCP tool로 Neo4j에 접근하고, Deep Agent/Ollama는 사용하지 않습니다.
# ============================================================
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


# 질문에서 시작일과 종료일을 찾아 YYYY-MM-DD 범위로 변환합니다.
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


# 숫자 값을 안전하게 float로 변환합니다.
def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


# Neo4j Record 하나를 보고서에 넣기 쉬운 짧은 문장으로 정리합니다.
def _format_record(record: dict) -> str:
    image = record.get("target_image") or record.get("filename") or record.get("image") or "unknown"
    date = record.get("date") or record.get("timestamp") or "unknown"
    total_change = record.get("total_change", record.get("total", "N/A"))
    speed = record.get("daily_change_speed_per_day", "N/A")
    change = record.get("change", "N/A")
    strong_change = record.get("strong_change", "N/A")
    return (
        f"- {image} ({date})\n"
        f"  total_change: {total_change}% / change: {change}% / strong_change: {strong_change}%\n"
        f"  daily_change_speed_per_day: {speed}%p/day"
    )


# Report 질문 키워드를 기준으로 실행할 MCP tool 계획을 만듭니다.
def select_report_plan(question: str) -> list[tuple[str, dict, str]]:
    plan: list[tuple[str, dict, str]] = []
    
    if any(word in question for word in ["전체", "분석", "보고", "요약", "통계", "리포트", "추세"]):
        plan.append(("get_statistics", {}, "전체 통계 조회"))
        plan.append(("search_top_change", {"order": "desc", "limit": 3}, "최대 변화 사례 조회"))
        plan.append(("search_top_change", {"order": "asc", "limit": 3}, "최소 변화 사례 조회"))
        plan.append(("search_by_speed", {"order": "desc", "limit": 3}, "변화 속도 상위 사례 조회"))
        return plan



    date_range = _extract_date_range(question)
    if date_range:
        start_date, end_date = date_range
        plan.append(("search_by_date_range", {"start_date": start_date, "end_date": end_date}, "기간별 변화 데이터 조회"))
        if any(word in question for word in ["속도", "변화 속도", "빠른", "느린"]):
            order = "asc" if any(word in question for word in ["느린", "최소", "낮은"]) else "desc"
            plan.append(("search_by_speed", {"order": order, "limit": 5}, "변화 속도 보조 조회"))
        return plan

    if any(word in question for word in ["속도", "변화 속도", "빠른", "느린"]):
        order = "asc" if any(word in question for word in ["느린", "최소", "낮은"]) else "desc"
        plan.append(("search_by_speed", {"order": order, "limit": 5}, "변화 속도 보고"))
        return plan

    if any(word in question for word in ["가장 높은", "최고", "최대", "상위", "많이"]):
        plan.append(("search_top_change", {"order": "desc", "limit": 5}, "변화율 상위 보고"))
        return plan

    if any(word in question for word in ["가장 낮은", "최저", "최소", "하위", "적은"]):
        plan.append(("search_top_change", {"order": "asc", "limit": 5}, "변화율 하위 보고"))
        return plan

    return []


# 선택된 MCP tool을 실제로 실행.
async def call_mcp_tool(tool_name: str, args: dict) -> str:
    tool = neo4j_tools_by_name.get(tool_name)
    if tool is None:
        available = ", ".join(sorted(neo4j_tools_by_name))
        raise RuntimeError(f"MCP tool을 찾지 못했습니다: {tool_name}. available={available}")

    result = await asyncio.wait_for(tool.ainvoke(args), timeout=A2A_TIMEOUT)
    return _stringify_tool_result(result)


# MCP tool 실행 계획을 순서대로 실행합니다.
async def run_report_plan(plan: list[tuple[str, dict, str]]) -> list[dict]:
    results = []
    for tool_name, args, reason in plan:
        print("=" * 60, flush=True)
        print(f"[Report Agent] MCP tool 선택: {tool_name}", flush=True)
        print(f"[Report Agent] 선택 이유: {reason}", flush=True)
        print(f"[Report Agent] tool args: {args}", flush=True)
        print("=" * 60, flush=True)

        raw_text = await call_mcp_tool(tool_name, args)
        print(f"[Report Agent] MCP tool 완료: {tool_name}", flush=True)
        results.append({"tool": tool_name, "args": args, "reason": reason, "raw": raw_text, "parsed": _parse_json_result(raw_text)})
    return results


# get_statistics 결과를 보고서용 통계 문장으로 정리합니다.
def _build_statistics_section(parsed: dict, speed_top: dict | None = None) -> str:
    if not isinstance(parsed, dict):
        return "- 전체 통계 결과를 해석하지 못했습니다."

    lines = ["## 전체 통계"]
    total = parsed.get("총_이미지_수") or parsed.get("total")
    avg = parsed.get("평균_변화율") or parsed.get("avg_total")
    max_total = parsed.get("최대_변화율") or parsed.get("max_total")
    min_total = parsed.get("최소_변화율") or parsed.get("min_total")

    if total is not None:
        lines.append(f"- 전체 Record 수: {total}개")
    if avg is not None:
        lines.append(f"- 평균 전체 변화율: {_safe_float(avg):.2f}%")
    if max_total is not None:
        lines.append(f"- 최대 변화율: {_safe_float(max_total):.2f}%")
    if min_total is not None:
        lines.append(f"- 최소 변화율: {_safe_float(min_total):.2f}%")

    max_image = parsed.get("최대_변화_이미지")
    if isinstance(max_image, dict):
        lines.append(f"- 최대 변화 이미지: {max_image.get('image', 'unknown')} ({max_image.get('date', 'unknown')})")

    min_image = parsed.get("최소_변화_이미지")
    if isinstance(min_image, dict):
        lines.append(f"- 최소 변화 이미지: {min_image.get('image', 'unknown')} ({min_image.get('date', 'unknown')})")

    if speed_top:
        image = speed_top.get("target_image") or speed_top.get("filename") or "unknown"
        date = speed_top.get("date") or speed_top.get("timestamp") or "unknown"
        speed = speed_top.get("daily_change_speed_per_day", "N/A")
        total_change = speed_top.get("total_change", "N/A")
        lines.append(
            f"- 변화 속도 가장 빠른 이미지: {image} ({date}, "
            f"{speed}%p/day, total_change {total_change}%)"
        )

    return "\n".join(lines)


# record list 결과를 보고서 섹션으로 정리합니다.
def _build_records_section(title: str, parsed) -> str:
    if not isinstance(parsed, list):
        return f"## {title}\n- 결과를 해석하지 못했습니다."
    if not parsed:
        return f"## {title}\n- 조건에 맞는 Record가 없습니다."
    return f"## {title}\n" + "\n".join(_format_record(record) for record in parsed[:5])


# 변화 속도 조회 결과에서 가장 빠른/느린 대표 record를 요약합니다.
def _build_speed_summary_section(parsed, order: str) -> str:
    if not isinstance(parsed, list) or not parsed:
        return "## 변화 속도 요약\n- 변화 속도 record를 찾지 못했습니다."

    top = parsed[0]
    label = "가장 느린" if order == "asc" else "가장 빠른"
    image = top.get("target_image") or top.get("filename") or "unknown"
    date = top.get("date") or top.get("timestamp") or "unknown"
    speed = top.get("daily_change_speed_per_day", "N/A")
    total_change = top.get("total_change", "N/A")
    return (
        f"## 변화 속도 {label} 요약\n"
        f"- {label} 이미지: {image}\n"
        f"- 날짜: {date}\n"
        f"- 변화 속도(daily_change_speed_per_day): {speed}%p/day\n"
        f"- 전체 변화율(total_change): {total_change}%"
    )


# MCP tool 결과들을 최종 리포트 답변으로 조립합니다.
def build_report_answer(question: str, results: list[dict]) -> str:
    sections = ["전체 KG 분석 리포트입니다."]
    speed_top = None
    for item in results:
        if item["tool"] == "search_by_speed" and item["args"].get("order") == "desc":
            parsed = item["parsed"]
            if isinstance(parsed, list) and parsed:
                speed_top = parsed[0]
                break

    for item in results:
        tool_name = item["tool"]
        args = item["args"]
        parsed = item["parsed"]
        reason = item["reason"]

        if tool_name == "get_statistics":
            sections.append(_build_statistics_section(parsed, speed_top=speed_top))
        elif tool_name == "search_top_change":
            title = "변화율 하위 사례" if args.get("order") == "asc" else "변화율 상위 사례"
            sections.append(_build_records_section(title, parsed))
        elif tool_name == "search_by_speed":
            title = "변화 속도 하위 사례" if args.get("order") == "asc" else "변화 속도 상위 사례"
            sections.append(_build_records_section(title, parsed))
        elif tool_name == "search_by_date_range":
            title = f"기간별 조회 결과 ({args['start_date']} ~ {args['end_date']})"
            sections.append(_build_records_section(title, parsed))
        else:
            sections.append(f"## {reason}\n{item['raw']}")

    sections.append("요약: 위 결과는 Neo4j KG에 저장된 Record를 MCP tool로 조회해 정리한 것입니다.")
    return "\n\n".join(sections)


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
    print("[Report Agent] Neo4j tools:", sorted(neo4j_tools_by_name), flush=True)
    print("Report Agent initialized without Deep Agent", flush=True)

    try:
        yield
    finally:
        if mcp_client is not None:
            await mcp_client.__aexit__(None, None, None)
        print("Report Agent stopped", flush=True)


app = FastAPI(title="Report Agent", lifespan=lifespan)


class A2ATask(BaseModel):
    task_id: str = ""
    sender: str = ""
    message: str = ""


class A2AResponse(BaseModel):
    task_id: str
    status: str
    result: dict


# A2A 클라이언트가 Report Agent 정보를 확인하는 엔드포인트입니다.
@app.get("/.well-known/agent-card")
async def agent_card():
    return {
        "name": "Report Agent",
        "description": "단백질 합성 KG 리포트를 rule-based MCP tool 호출로 생성합니다.",
        "version": "1.0",
        "url": "http://localhost:8002",
        "capabilities": ["report", "statistics", "trend", "mcp_tool_calling"],
        "endpoints": {"task": "/task"},
        "tools": ["get_statistics", "search_top_change", "search_by_date_range", "search_by_speed"],
    }


# Supervisor가 보낸 리포트 요청을 MCP tool 호출 결과로 답변합니다.
@app.post("/task", response_model=A2AResponse)
async def handle_task(task: A2ATask):
    task_id = task.task_id or str(uuid.uuid4())
    print("[Report Agent] /task 요청 받음:", task.message, flush=True)

    try:
        plan = select_report_plan(task.message)
        if not plan:
            answer = (
                "현재 Report Agent는 안정적인 MCP tool 직접 호출 방식으로 동작합니다. "
                "전체 분석/통계/요약, 기간 분석, 변화율 상위/하위, 변화 속도 보고 중 하나로 질문해 주세요."
            )
            return A2AResponse(task_id=task_id, status="completed", result={"answer": answer})

        results = await run_report_plan(plan)
        answer = build_report_answer(task.message, results)
        print("[Report Agent] 답변 생성 완료", flush=True)

        return A2AResponse(task_id=task_id, status="completed", result={"answer": answer})
    
    except Exception as e:
        print("[Report Agent] 오류 발생:", repr(e), type(e).__name__, flush=True)
        return A2AResponse(task_id=task_id, status="failed", result={"error": str(e) or type(e).__name__})


# 서버가 살아 있는지 확인하는 health check 엔드포인트입니다.
@app.get("/health")
async def health():
    return {"status": "ok", "agent": "Report Agent", "time": str(datetime.now())}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
