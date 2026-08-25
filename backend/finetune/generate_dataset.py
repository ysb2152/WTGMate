"""
1단계: Gemini를 "교사 모델"로 써서 파인튜닝용 합성 데이터를 생성한다.

목표 태스크는 backend/main.py 의 /api/parse-tasks 와 완전히 동일하다:
    자연어 일정 문장 -> 방문 장소 JSON 배열 (name, task, priority, lat, lng, address)

여기서 만든 데이터로 2단계(로컬 소형 모델 LoRA 파인튜닝)를 진행하고,
학습이 끝나면 그 모델을 Ollama로 서빙해서 backend/main.py 의 Gemini 호출부를
그대로 대체하는 것이 최종 목표다.

사용법 (backend 디렉터리에서, venv 활성화 후):
    python finetune/generate_dataset.py --count 200

환경변수는 backend/.env 를 그대로 재사용한다 (GEMINI_API_KEY, GEMINI_MODEL).

출력:
    finetune/data/raw_examples.jsonl   - 생성된 원본 예시 (디버깅/검수용)
    finetune/data/alpaca_dataset.jsonl - 2단계 LoRA 학습에 바로 넣을 수 있는 형식
"""
import argparse
import json
import time
from pathlib import Path

from dotenv import load_dotenv
import os

import google.generativeai as genai

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_PATH = DATA_DIR / "raw_examples.jsonl"
ALPACA_PATH = DATA_DIR / "alpaca_dataset.jsonl"

# backend/main.py 의 parse_tasks 프롬프트 지시문과 완전히 동일하게 유지한다.
# (파인튜닝 모델도 결국 같은 지시문으로 호출될 것이므로 학습/추론 프롬프트를 맞춰야 한다)
TASK_INSTRUCTION = """아래 일정 문장에서 방문해야 할 장소를 모두 추출해 JSON 배열로 반환해줘.

각 항목은 반드시 다음 필드를 가져야 한다.
- name: 장소명
- task: 그 장소에서 해야 할 일
- priority: 중요도 1~5, 1이 가장 중요함
- lat: 숫자
- lng: 숫자
- address: 알고 있다면 주소, 모르면 빈 문자열

장소명이 애매하면 가장 유력한 장소명을 사용한다.
응답에는 JSON 배열만 포함한다."""

REQUIRED_KEYS = {"name", "task", "priority", "lat", "lng", "address"}

REGIONS = ["서울", "부산", "대구", "인천", "광주", "대전", "수원", "제주", "강릉", "전주", "울산", "창원"]

GEN_PROMPT_TEMPLATE = """너는 LLM 파인튜닝용 학습 데이터를 만드는 도우미다.
아래 조건을 만족하는 학습 예시를 정확히 {batch_size}개 만들어라.

각 예시는 다음 두 가지로 구성된다.
1. "input": 사용자가 캘린더/할일 앱에 입력할 법한 자연스러운 한국어 일정 문장 하나.
   - 하루 일정에 {min_loc}~{max_loc}개의 방문 장소가 자연스럽게 섞여 있어야 한다.
   - 문장 스타일(반말/존댓말/메모체/구어체)을 예시마다 다양하게 섞어라.
   - 장소는 "{region}" 지역 근처의 실제 지하철역, 랜드마크, 프랜차이즈, 관공서 등을 우선 사용하라.
   - 문장 안에 우선순위가 자연스럽게 암시되도록 하라 (예: "꼭", "중요한", "시간 되면", "여유있게", "급한 건 아니지만" 등).
2. "output": 아래 지시문을 이 input에 대해 정확히 수행했을 때 나와야 하는 정답 JSON 배열.

--- 지시문 ---
{task_instruction}
--- 지시문 끝 ---

주의사항:
- output의 lat/lng는 실제 해당 장소의 위도/경도를 최대한 정확한 실제 값으로 채워라. 정확히 모르면 그 지역 중심부의 근사 좌표를 사용하라.
- input에서 언급된 장소 개수와 output 배열의 길이는 반드시 일치해야 한다.
- 같은 장소명을 여러 예시에서 반복해서 쓰지 말고 다양화하라.
- 아래 JSON 스키마로만 응답하라. 코드블록이나 설명 등 다른 텍스트는 절대 포함하지 마라.

{{"examples": [{{"input": "...", "output": [{{"name": "...", "task": "...", "priority": 1, "lat": 0.0, "lng": 0.0, "address": "..."}}]}}]}}
"""


def validate_example(ex: dict) -> bool:
    if not isinstance(ex, dict):
        return False
    if not isinstance(ex.get("input"), str) or not ex["input"].strip():
        return False
    output = ex.get("output")
    if not isinstance(output, list) or not output:
        return False
    for item in output:
        if not isinstance(item, dict):
            return False
        if not REQUIRED_KEYS.issubset(item.keys()):
            return False
        try:
            float(item["lat"])
            float(item["lng"])
            int(item["priority"])
        except (TypeError, ValueError):
            return False
    return True


def generate_batch(model, batch_size: int, min_loc: int, max_loc: int, region: str) -> list[dict]:
    prompt = GEN_PROMPT_TEMPLATE.format(
        batch_size=batch_size,
        min_loc=min_loc,
        max_loc=max_loc,
        region=region,
        task_instruction=TASK_INSTRUCTION,
    )
    response = model.generate_content(prompt)
    parsed = json.loads(response.text)
    examples = parsed.get("examples", [])
    return [ex for ex in examples if validate_example(ex)]


def to_alpaca(ex: dict) -> dict:
    return {
        "instruction": TASK_INSTRUCTION,
        "input": ex["input"],
        "output": json.dumps(ex["output"], ensure_ascii=False),
    }


def main():
    parser = argparse.ArgumentParser(description="Gemini로 파인튜닝용 합성 데이터 생성")
    parser.add_argument("--count", type=int, default=200, help="목표 예시 개수 (기본 200)")
    parser.add_argument("--batch-size", type=int, default=10, help="API 호출 1회당 생성 개수")
    parser.add_argument("--min-loc", type=int, default=1)
    parser.add_argument("--max-loc", type=int, default=5)
    parser.add_argument("--max-retries", type=int, default=3, help="배치 1개당 최대 재시도 횟수")
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="데이터 생성에 쓸 모델명. 지정 안 하면 .env의 GEMINI_MODEL(운영용) 사용. "
             "운영용 모델의 무료 일일 쿼터가 빠듯하면 flash-lite 계열로 분리 지정 권장.",
    )
    args = parser.parse_args()

    load_dotenv(BASE_DIR.parent / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = args.model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if not api_key:
        raise SystemExit("backend/.env 에 GEMINI_API_KEY가 없습니다. 먼저 설정해주세요.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name,
        generation_config={"response_mime_type": "application/json"},
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 이미 만든 input은 중복 생성하지 않도록 이어쓰기 지원
    seen_inputs = set()
    if RAW_PATH.exists():
        with RAW_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                seen_inputs.add(json.loads(line)["input"])

    collected = len(seen_inputs)
    print(f"기존 예시 {collected}개 발견, 목표 {args.count}개까지 생성합니다.")

    with RAW_PATH.open("a", encoding="utf-8") as raw_f, ALPACA_PATH.open("a", encoding="utf-8") as alpaca_f:
        region_i = 0
        while collected < args.count:
            region = REGIONS[region_i % len(REGIONS)]
            region_i += 1
            batch_size = min(args.batch_size, args.count - collected)

            batch = None
            for attempt in range(1, args.max_retries + 1):
                try:
                    batch = generate_batch(model, batch_size, args.min_loc, args.max_loc, region)
                    break
                except Exception as e:
                    msg = str(e)
                    if "GenerateRequestsPerDayPerProjectPerModel" in msg:
                        # 일일 쿼터 소진: 재시도해도 오늘은 안 풀리므로 즉시 중단
                        print(f"  [중단] '{model_name}' 모델의 무료 일일 쿼터를 모두 썼습니다.")
                        print(f"  누적 {collected}개까지 저장됨. 다른 모델(--model)을 쓰거나 내일 다시 시도하세요.")
                        return
                    wait = 2 ** attempt
                    print(f"  [경고] 배치 생성 실패 ({region}, 시도 {attempt}/{args.max_retries}): {e} -> {wait}s 대기")
                    time.sleep(wait)
            if batch is None:
                print(f"  [건너뜀] {region} 배치를 포기합니다.")
                continue

            new_count = 0
            for ex in batch:
                if ex["input"] in seen_inputs:
                    continue
                seen_inputs.add(ex["input"])
                raw_f.write(json.dumps(ex, ensure_ascii=False) + "\n")
                alpaca_f.write(json.dumps(to_alpaca(ex), ensure_ascii=False) + "\n")
                new_count += 1

            raw_f.flush()
            alpaca_f.flush()
            collected += new_count
            print(f"[{region}] +{new_count}개 (누적 {collected}/{args.count})")

    print(f"완료. {RAW_PATH} / {ALPACA_PATH} 에 저장됨.")


if __name__ == "__main__":
    main()
