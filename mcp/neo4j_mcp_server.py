# mcp/neo4j_mcp_server.py
from fastmcp import FastMCP
from neo4j import GraphDatabase
import json, re, sys, os
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import NEO4J_URI, NEO4J_AUTH

driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
mcp = FastMCP("Neo4j KG Server")

# ============================================================
# Tool 1: 날짜 기반 검색
# ============================================================
@mcp.tool()
def search_by_date(date: str) -> str:
    """
    특정 날짜의 단백질 합성 변화 데이터를 검색합니다.
    date 형식: YYYY-MM-DD (예: 2025-10-13)
    """
    with driver.session() as session:
        result = session.run("""
            MATCH (n:Record)
            WHERE n.date = $date
            RETURN n
            ORDER BY n.total_change DESC
        """, date=date)
        records = [dict(r["n"]) for r in result]
        if not records:
            return f"{date} 데이터 없음"
        return json.dumps(records, ensure_ascii=False, indent=2)


# ============================================================
# Tool 2: 변화율 최대/최소 검색
# ============================================================
@mcp.tool()
def search_top_change(order: str = "desc", limit: int = 5) -> str:
    """
    변화율 기준 상위/하위 데이터를 검색합니다.
    order: desc (높은 순) / asc (낮은 순)
    """
    order = order.upper()
    if order not in ["ASC", "DESC"]:
        order = "DESC"
    with driver.session() as session:
        result = session.run(f"""
            MATCH (n:Record)
            RETURN n
            ORDER BY n.total_change {order}
            LIMIT $limit
        """, limit=limit)
        records = [dict(r["n"]) for r in result]
        return json.dumps(records, ensure_ascii=False, indent=2)


# ============================================================
# Tool 3: 날짜 범위 검색
# ============================================================
@mcp.tool()
def search_by_date_range(start_date: str, end_date: str) -> str:
    """
    날짜 범위 내 변화 데이터를 검색합니다.
    형식: YYYY-MM-DD
    """
    with driver.session() as session:
        result = session.run("""
            MATCH (n:Record)
            WHERE n.date >= $start AND n.date <= $end
            RETURN n
            ORDER BY n.total_change DESC
        """, start=start_date, end=end_date)
        records = [dict(r["n"]) for r in result]
        if not records:
            return f"{start_date} ~ {end_date} 데이터 없음"
        return json.dumps(records, ensure_ascii=False, indent=2)


# ============================================================
# Tool 4: 변화 속도 검색
# ============================================================
@mcp.tool()
def search_by_speed(order: str = "desc", limit: int = 5) -> str:
    """
    변화 속도 기준 데이터를 검색합니다.
    order: desc (빠른 순) / asc (느린 순)
    """
    order = order.upper()
    if order not in ["ASC", "DESC"]:
        order = "DESC"
    with driver.session() as session:
        result = session.run(f"""
            MATCH (n:Record)
            WHERE n.daily_change_speed_per_day IS NOT NULL
            RETURN n
            ORDER BY n.daily_change_speed_per_day {order}
            LIMIT $limit
        """, limit=limit)
        records = [dict(r["n"]) for r in result]
        return json.dumps(records, ensure_ascii=False, indent=2)


# ============================================================
# Tool 5: 전체 통계
@mcp.tool()
def get_statistics() -> str:
    """전체 단백질 합성 변화 통계를 반환합니다."""
    with driver.session() as session:
        stats = dict(session.run("""
            MATCH (n:Record)
            RETURN count(n) as total,
                   avg(n.total_change) as avg_total,
                   max(n.total_change) as max_total,
                   min(n.total_change) as min_total
        """).single())

        max_r = dict(session.run("""
            MATCH (n:Record)
            RETURN n.target_image as image, n.date as date, n.total_change as total
            ORDER BY n.total_change DESC LIMIT 1
        """).single())

        min_r = dict(session.run("""
            MATCH (n:Record)
            RETURN n.target_image as image, n.date as date, n.total_change as total
            ORDER BY n.total_change ASC LIMIT 1
        """).single())

        result = {
            "총_이미지_수":    int(stats["total"]),
            "평균_변화율":     round(float(stats["avg_total"] or 0), 2),
            "최대_변화율":     round(float(stats["max_total"] or 0), 2),
            "최소_변화율":     round(float(stats["min_total"] or 0), 2),
            "최대_변화_이미지": max_r,
            "최소_변화_이미지": min_r
        }
        return json.dumps(result, ensure_ascii=False, indent=2)


# ============================================================
# Tool 6: 그래프 탐색
# ============================================================
@mcp.tool()
def explore_graph(node_id: str) -> str:
    """
    특정 노드와 연결된 관계를 탐색합니다.
    예시: ChangeEvent_Camera1_20251013_045900
    """
    with driver.session() as session:
        result = session.run("""
            MATCH (n {node_id: $node_id})-[r]->(m)
            RETURN type(r) as relation,
                   labels(m)[0] as target_type,
                   m.node_id as target_id,
                   properties(m) as target_props
        """, node_id=node_id)
        relations = [dict(r) for r in result]
        if not relations:
            return f"{node_id} 연결 관계 없음"
        return json.dumps(relations, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()