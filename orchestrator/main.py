# orchestrator/main.py
# ============================================================
# Supervisor - A2A 라우팅만 담당
# ============================================================
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import KG_AGENT_URL, REPORT_AGENT_URL

from supervisor import route
from a2a_client import send_a2a_task, analyze_image_kaggle


# ============================================================
# 메인 실행
# ============================================================
def run(question: str, image_path: str = None) -> str:
    has_image = image_path is not None and str(image_path).strip() != ""
    agent = route(question, has_image)

    print(f"[Supervisor] route → {agent}")

    if agent == "IMAGE_AGENT":
        result = analyze_image_kaggle(image_path)
        return result.get("vlm_report", str(result))

    elif agent == "KG_AGENT":
        response = send_a2a_task(KG_AGENT_URL, question)
        return response.get("result", {}).get("answer", str(response))

    elif agent == "REPORT_AGENT":
        response = send_a2a_task(REPORT_AGENT_URL, question)
        return response.get("result", {}).get("answer", str(response))

    return "라우팅 실패"


if __name__ == "__main__":
    print("=== 단백질 합성 분석 Supervisor ===")
    print("종료: exit\n")

    while True:
        try:
            question = input("질문: ").strip()
            if not question:
                continue
            if question.lower() in ["exit", "quit", "종료"]:
                print("종료합니다.")
                break

            print("\n분석 중...\n")
            answer = run(question)
            print(f"\n답변: {answer}\n")

        except KeyboardInterrupt:
            print("\n종료합니다.")
            break
        except Exception as e:
            print(f"오류: {str(e)}")