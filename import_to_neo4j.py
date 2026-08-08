import json
from neo4j import GraphDatabase
from config import NEO4J_URI, NEO4J_AUTH

JSON_PATH = "kg_graph_JSON.json"


def safe_float(v):
    return None if v is None else float(v)


def safe_int(v):
    return None if v is None else int(v)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_constraints(tx):
    queries = [
        "CREATE CONSTRAINT changeevent_id IF NOT EXISTS FOR (n:ChangeEvent) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT image_id IF NOT EXISTS FOR (n:Image) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT date_id IF NOT EXISTS FOR (n:Date) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT changelevel_id IF NOT EXISTS FOR (n:ChangeLevel) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT strongchangelevel_id IF NOT EXISTS FOR (n:StrongChangeLevel) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT speedlevel_id IF NOT EXISTS FOR (n:SpeedLevel) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT referenceimage_id IF NOT EXISTS FOR (n:ReferenceImage) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT record_node_id IF NOT EXISTS FOR (n:Record) REQUIRE n.node_id IS UNIQUE",
    ]
    for q in queries:
        tx.run(q)


def create_node(tx, node):
    node_id = node["id"]
    node_type = node["type"]
    props = node.get("properties", {}).copy()

    # Neo4j 노드 공통 id 부여
    props["id"] = node_id

    query = f"""
    MERGE (n:{node_type} {{id: $id}})
    SET n += $props
    """
    tx.run(query, id=node_id, props=props)


def create_relationship(tx, rel):
    source = rel["source"]
    target = rel["target"]
    rel_type = rel["relation"]

    query = f"""
    MATCH (a {{id: $source}})
    MATCH (b {{id: $target}})
    MERGE (a)-[r:{rel_type}]->(b)
    """
    tx.run(query, source=source, target=target)


def build_lookup_maps(data):
    node_map = {n["id"]: n for n in data["nodes"]}

    # ChangeEvent -> Image / Date 연결 찾기
    event_to_image = {}
    event_to_date = {}

    for rel in data["edges"]:
        if rel["relation"] == "TARGET_IMAGE":
            event_to_image[rel["source"]] = rel["target"]
        elif rel["relation"] == "OCCURRED_ON":
            event_to_date[rel["source"]] = rel["target"]

    return node_map, event_to_image, event_to_date


def create_record_nodes(tx, data):
    node_map, event_to_image, event_to_date = build_lookup_maps(data)

    for node in data["nodes"]:
        if node["type"] != "ChangeEvent":
            continue

        event_id = node["id"]
        props = node.get("properties", {})

        image_id = event_to_image.get(event_id)
        date_id = event_to_date.get(event_id)

        image_filename = None
        if image_id and image_id in node_map:
            image_filename = node_map[image_id].get("properties", {}).get("filename")

        date_value = None
        if date_id and date_id in node_map:
            date_value = node_map[date_id].get("properties", {}).get("date")

        record_props = {
            "node_id": event_id,
            "change_event_id": props.get("change_event_id"),
            "date": date_value,
            "target_image": image_filename,
            "change": safe_float(props.get("change")),
            "strong_change": safe_float(props.get("strong_change")),
            "total_change": safe_float(props.get("total_change")),
            "daily_avg_total_change": safe_float(props.get("daily_avg_total_change")),
            "previous_date": props.get("previous_date"),
            "previous_avg_total_change": safe_float(props.get("previous_avg_total_change")),
            "days_from_previous": safe_int(props.get("days_from_previous")),
            "daily_change_delta": safe_float(props.get("daily_change_delta")),
            "daily_change_speed_per_day": safe_float(props.get("daily_change_speed_per_day")),
        }

        tx.run(
            """
            MERGE (r:Record {node_id: $node_id})
            SET r += $props
            """,
            node_id=event_id,
            props=record_props,
        )

        # Record와 원본 ChangeEvent 연결도 만들어두면 나중에 보기 편함
        tx.run(
            """
            MATCH (r:Record {node_id: $node_id})
            MATCH (e:ChangeEvent {id: $event_id})
            MERGE (r)-[:SOURCE_EVENT]->(e)
            """,
            node_id=event_id,
            event_id=event_id,
        )


def main():
    data = load_json(JSON_PATH)

    driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

    with driver.session() as session:
        print("1) 제약조건 생성 중...")
        session.execute_write(create_constraints)

        print("2) 기존 데이터 삭제 중...")
        session.run("MATCH (n) DETACH DELETE n")

        print("3) KG 노드 적재 중...")
        for node in data["nodes"]:
            session.execute_write(create_node, node)

        print("4) KG 관계 적재 중...")
        for rel in data["edges"]:
            session.execute_write(create_relationship, rel)

        print("5) Record 노드 생성 중...")
        session.execute_write(create_record_nodes, data)

        # 결과 확인
        node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        record_count = session.run("MATCH (r:Record) RETURN count(r) AS c").single()["c"]

        print("\n=== 적재 완료 ===")
        print(f"총 노드 수: {node_count}")
        print(f"총 관계 수: {rel_count}")
        print(f"Record 노드 수: {record_count}")

    driver.close()


if __name__ == "__main__":
    main()