# 트러블슈팅 기록

프로젝트 점검 중 발견하고 수정한 핵심 이슈를 정리합니다.

## 요약

| # | 이슈 | 심각도 | 상태 |
|---|---|---|---|
| 1 | 자격증명 하드코딩 노출 (Neo4j×2, HF 토큰, ngrok×2) | Critical | 코드 수정 완료 · 재발급은 별도 필요 |
| 2 | Neo4j 인스턴스 불일치 (로컬 ↔ Kaggle이 서로 다른 DB 참조) | Critical | 코드 통일 완료 · DB 재생성 필요 |
| 3 | Kaggle FastAPI 엔드포인트 무인증 노출 | High | 수정 완료 |
| 4 | 깨진 import / 죽은 코드 | Medium | 수정 완료 |
| 5 | `import_to_neo4j.py` 파일 경로 불일치 | Medium | 수정 완료 |
| 6 | 날짜 파싱 연도 하드코딩 | Low | 수정 완료 |
| 7 | ngrok 인증 순서 버그 + 중복 터널 생성 | Low | 수정 완료 |

## 상세

### 1. 자격증명 하드코딩 노출 (Critical)
- **증상**: `config.py`와 `image_agent.ipynb`에 Neo4j 비밀번호(인스턴스 2개), HuggingFace 토큰, ngrok authtoken(2개)이 평문으로 박혀 있었음.
- **원인**: 프로토타이핑 단계에서 빠른 실행을 위해 코드에 직접 기입.
- **조치**: `python-dotenv` 기반 `.env`로 전환하고 `.gitignore` 처리, Kaggle 쪽은 `kaggle_secrets.UserSecretsClient`(Secrets)로 전환. 이미 노출됐던 값 자체가 무효화된 것은 아니므로 발급처에서 재발급 필요.

### 2. Neo4j 인스턴스 불일치 (Critical)
- **증상**: 로컬 에이전트(`config.py`)와 Kaggle 이미지 에이전트(notebook)가 서로 다른 AuraDB 인스턴스를 가리키고 있어, 이미지 분석 결과가 KG/Report Agent 조회에 반영되지 않는 구조였음.
- **진단**: 두 인스턴스 호스트명을 공용 DNS(8.8.8.8)로 직접 조회 → 둘 다 `NXDOMAIN`, 즉 이미 삭제된 인스턴스로 확인.
- **조치**: 코드상 참조는 하나로 통일. 실제 DB는 새 Aura 인스턴스를 만들어 `.env`에 반영해야 함 (외부 액션 필요, 미해결).

### 3. Kaggle FastAPI 엔드포인트 무인증 노출 (High)
- **증상**: ngrok으로 노출한 `/analyze`, `/search_kg`, `/report`, `/rag_search` 4개 엔드포인트에 인증이 없어 URL만 알면 누구나 호출 가능(GPU 추론 무단 실행, KG 무단 쓰기 등).
- **조치**: `X-API-Key` 헤더 검증 의존성 추가. 로컬 `.env`와 Kaggle Secrets에 동일한 공유 키(`KAGGLE_API_KEY`)를 등록해 매칭.

### 4. 깨진 import / 죽은 코드 (Medium)
- **증상**: `orchestrator/supervisor.py`가 존재하지 않는 `shared.lim_client`를 import(호출 시 즉시 에러). `orchestrator/a2a_client.py`는 어디서도 사용되지 않고 `main.py`가 같은 로직을 복붙해 중복 유지. `shared/rag_client.py`는 빈 파일.
- **조치**: `supervisor.py`에 실제 동작하던 Ollama 라우팅 로직을 이전해 정상화하고, `main.py`가 `supervisor.py`/`a2a_client.py`를 import하도록 변경해 중복 제거. 빈 파일 삭제.

### 5. `import_to_neo4j.py` 파일 경로 불일치 (Medium)
- **증상**: `JSON_PATH`가 `full_302_change_event_kg_graph_image.json`을 가리켰지만 실제 파일명은 `kg_graph_JSON.json` — 스크립트 실행 시 `FileNotFoundError`.
- **조치**: 실제 파일명으로 수정.

### 6. 날짜 파싱 연도 하드코딩 (Low)
- **증상**: `kg_agent_server.py` / `report_agent_server.py`에서 "8월 5일"처럼 연도가 없는 질문이 무조건 `2025`년으로 고정 변환됨.
- **조치**: `datetime.now().year` 기준으로 동적 계산하도록 변경.

### 7. ngrok 인증 순서 버그 + 중복 터널 (Low, 부수 발견)
- **증상**: notebook cell 30에서 `ngrok.set_auth_token()`이 `ngrok.connect()` **이후**에 호출되고 있었고, 같은 셀 안에서 8000번 포트로 터널을 두 번 열어 먼저 출력된 Public URL이 무효화되는 상태였음.
- **조치**: 인증 토큰 설정을 connect보다 먼저 실행하도록 순서 수정, 중복 터널 생성 코드 제거.

## 검증 방법
수정 후 매번 `py_compile` 문법 검사와 모듈 import 테스트를 거쳤고, 노트북은 `nbformat.validate`로 스키마 검증, 시크릿 패턴은 `grep`으로 전체 재검색해 잔존 여부를 확인했습니다.

## 남은 작업 (외부 액션 필요)
- Neo4j AuraDB 신규 인스턴스 생성 후 `.env` 반영
- HuggingFace 토큰 / ngrok authtoken 재발급
- Kaggle 노트북 Secrets 등록 후 재실행 → 새 ngrok URL을 `.env`에 반영
