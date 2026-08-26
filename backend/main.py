import os
import json
import math
import time
import asyncio
import itertools
from collections import OrderedDict
from typing import List, Optional, Dict, Tuple
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp


load_dotenv()

# backend/finetune/ 에서 파인튜닝해 Ollama에 등록한 로컬 모델을 사용한다 (Gemini 대체).
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "routemate-parser")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
# Tmap(SK) 보행자 경로안내 API 키. 있으면 도보를 실제 인도 경로/거리/시간으로 계산하고,
# 없으면 기존 추정값(fallback_leg)으로 동작한다.
TMAP_APP_KEY = os.getenv("TMAP_APP_KEY")


app = FastAPI(title="RouteMate API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# 튜닝 상수
# =========================================================

# 완전탐색(순열)을 쓸 수 있는 최대 "목적지" 개수 (출발지 제외).
# 9! = 362,880 로 파이썬 루프로도 1초 이내에 충분히 처리 가능.
# 이보다 많아지면 근사/휴리스틱 방식으로 자동 전환한다.
MAX_BRUTE_FORCE_STOPS = 9

# AI 추천 모드에서 "중요도 1점당, 방문 순서 1칸당" 부여할 페널티(초).
# priority는 1이 가장 중요 / 5가 가장 여유이므로, 그대로 곱하면 방향이 반대가 된다.
# (priority=1을 낮은 가중치로 취급해버려 "중요한 곳을 늦게 가도 페널티가 작아지는" 오류 발생)
# 그래서 importance = 6 - priority 로 뒤집어서 사용한다 (priority1 -> importance5, priority5 -> importance1).
# 즉 importance(priority) * position * AI_PRIORITY_WEIGHT_SEC 만큼 패널티가 붙어서,
# "중요한 장소일수록 늦게 방문할 때 페널티가 커지도록" 만든다.
# 값을 늘리면 "우선순위를 얼마나 강하게 반영할지"가 커진다.
# 튜닝 근거(2026-08, 실측 스윕): 60 미만이면 우선순위가 과소반영되어 "가장 중요한 곳"이
# 앞으로 오지 않고, 100을 넘어서면 순서가 우선순위 쪽으로 포화되어 AI 모드가 사실상
# priority 모드와 같아진다(이전 기본값 300은 이동시간을 약 5배 압도했다). 그 사이 60~100이
# "중요한 곳을 먼저 가되 나머지는 이동시간으로 최적화"하는 균형 구간이라, 하한(60)에서
# 여유를 둔 100을 기본값으로 쓴다(우선순위 지배력 약 1.8배).
AI_PRIORITY_WEIGHT_SEC = 100

# 약속 시각(appointment_time)을 어긴 순서에 부여하는 페널티(초).
# 어떤 이동시간/우선순위 비용보다도 압도적으로 크게 잡아, "약속을 지키는 순서"가
# 항상 그렇지 않은 순서보다 먼저 선택되도록 만든다(사실상 하드 제약).
APPOINTMENT_VIOLATION_PENALTY_SEC = 10_000_000

# 약속 시각 대비 허용하는 지각(분). 0이면 약속 시각까지 정확히 도착해야 한다.
APPOINTMENT_TOLERANCE_MIN = 0


# =========================================================
# Models
# =========================================================

class ParseRequest(BaseModel):
    user_text: str


class LocationItem(BaseModel):
    name: str
    task: str
    priority: Optional[int] = 3
    lat: float
    lng: float
    address: Optional[str] = ""
    # appointment_time: "HH:MM" 고정 약속 시각(= 도착 마감). LLM이 산문에서 추출한다.
    #   값이 있으면 그 시각까지 반드시 도착해야 하는 하드 제약으로 취급한다.
    appointment_time: Optional[str] = None
    # duration_min: 그 장소에서 머무는(체류) 시간(분). 사용자가 입력한다.
    duration_min: Optional[int] = 0


class OptimizeRequest(BaseModel):
    start_location: LocationItem
    locations: List[LocationItem]
    travel_mode: str = "car"
    # shortest : 우선순위 완전 무시, 순수 이동시간 최소화
    # priority : 우선순위 그룹 순서를 절대 기준으로 강제, 그룹 내에서만 이동시간 최소화
    # ai       : 이동시간 + (우선순위 x 방문순서) 페널티를 종합한 점수 최소화
    optimize_mode: str = "ai"
    # start_time: "HH:MM" 예상 출발 시각(사용자 입력). 지금일 수도, 내일일 수도 있으므로
    #   "현재 시각"이 아니라 사용자가 정한 출발 예정 시각으로 다룬다.
    #   있으면 약속 시각 제약과 도착 시각 계산에 사용한다.
    start_time: Optional[str] = None


class RouteDetailRequest(BaseModel):
    ordered_locations: List[LocationItem]
    travel_mode: str = "car"
    # start_time: "HH:MM" 예상 출발 시각. 있으면 각 장소의 도착/출발 시각을 계산해 반환한다.
    start_time: Optional[str] = None


# =========================================================
# Common helpers
# =========================================================

def clamp_priority(value: Optional[int]) -> int:
    try:
        # `value or 3`는 value=0일 때도 falsy로 취급되어 3으로 바뀌어버리므로
        # None인 경우에만 기본값 3을 쓰도록 명시적으로 검사한다.
        value = int(value) if value is not None else 3
    except (TypeError, ValueError):
        value = 3
    return max(1, min(5, value))


def importance_score(priority: Optional[int]) -> int:
    """priority(1=가장 중요 ~ 5=가장 여유)를 '중요도 가중치'로 뒤집는다.
    priority=1 -> 5 (가중치 최대), priority=5 -> 1 (가중치 최소)."""
    return 6 - clamp_priority(priority)


def validate_locations(locations: List[LocationItem]):
    for index, loc in enumerate(locations):
        if not math.isfinite(loc.lat) or not math.isfinite(loc.lng):
            raise HTTPException(
                status_code=400,
                detail=f"{index}번째 장소 [{loc.name}]의 좌표가 올바르지 않습니다.",
            )


def parse_hhmm(value: Optional[str]) -> Optional[int]:
    """'HH:MM' 문자열을 자정 기준 분(minute)으로 변환. 형식이 잘못되면 None."""
    if not value:
        return None
    try:
        parts = str(value).strip().split(":")
        if len(parts) != 2:
            return None
        total = int(parts[0]) * 60 + int(parts[1])
    except (TypeError, ValueError):
        return None
    if 0 <= total < 24 * 60:
        return total
    return None


def format_hhmm(minutes: Optional[float]) -> Optional[str]:
    """분(minute)을 'HH:MM'로 변환. 자정을 넘어가면 24시간으로 나눈 나머지로 표기(같은 날 가정)."""
    if minutes is None:
        return None
    m = int(round(minutes)) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def build_schedule(
    full_order: List[int],
    all_locations: List[LocationItem],
    time_matrix: List[List[int]],
    start_min: Optional[int],
):
    """방문 순서(출발지 0을 맨 앞에 포함)를 따라 시간축을 계산한다.

    각 정거장에서: 도착 = 직전 출발 + 이동시간. 약속 시각이 있으면
    - 일찍 도착하면 약속 시각까지 대기(그때부터 체류 시작),
    - 약속 시각(+허용 지각)을 넘겨 도착하면 위반으로 기록.
    이후 체류시간(duration_min)만큼 머문 뒤 다음 장소로 출발한다.

    반환: (schedule, violations, finish_min)
      schedule: [{node, arrival_min, depart_min, appointment_min, late}]
      violations: 약속을 어긴 node 인덱스 목록
      finish_min: 마지막 장소에서의 최종 출발(=일정 종료) 시각
    """
    schedule = []
    violations = []
    t: Optional[float] = start_min

    for pos, node in enumerate(full_order):
        appt = parse_hhmm(getattr(all_locations[node], "appointment_time", None))
        late = False

        if pos == 0:
            arrival = t  # 출발지: 도착=출발 시각
        else:
            prev = full_order[pos - 1]
            if t is not None:
                t += time_matrix[prev][node] / 60.0
            arrival = t
            if t is not None and appt is not None:
                if arrival > appt + APPOINTMENT_TOLERANCE_MIN:
                    late = True
                    violations.append(node)
                elif arrival < appt:
                    t = appt  # 일찍 도착 -> 약속 시각까지 대기

        dwell = int(getattr(all_locations[node], "duration_min", 0) or 0)
        if t is not None:
            t += dwell

        schedule.append({
            "node": node,
            "arrival_min": arrival,
            "depart_min": t,
            "appointment_min": appt,
            "late": late,
        })

    return schedule, violations, t


def chronological_penalty_sec(stop_order: List[int], all_locations: List[LocationItem]) -> int:
    """출발 시각을 모를 때 쓰는 '약속 시간순 정렬' 페널티.
    도착 시각/지각은 계산할 수 없으므로, 약속 시각이 입력된 장소들끼리만
    '이른 약속 -> 늦은 약속' 순서가 되도록 역순 쌍(inversion) 1개당 큰 페널티를 준다.
    (약속이 0~1개면 항상 0)"""
    appt_seq = [
        m
        for node in stop_order
        if (m := parse_hhmm(getattr(all_locations[node], "appointment_time", None))) is not None
    ]
    inversions = sum(
        1
        for i in range(len(appt_seq))
        for j in range(i + 1, len(appt_seq))
        if appt_seq[i] > appt_seq[j]
    )
    return inversions * APPOINTMENT_VIOLATION_PENALTY_SEC


def appointment_penalty_sec(
    stop_order: List[int],
    all_locations: List[LocationItem],
    time_matrix: List[List[int]],
    start_min: Optional[int],
) -> int:
    """주어진 목적지 방문 순서(출발지 제외)의 약속 관련 페널티(초).
    - 출발 시각이 있으면: 실제 도착 시각을 계산해 지각(약속 위반) 개수에 비례한 페널티.
    - 출발 시각이 없으면: 도착 계산이 불가하므로 약속 '시간순 정렬'만 유도하는 페널티."""
    if start_min is None:
        return chronological_penalty_sec(stop_order, all_locations)
    _, violations, _ = build_schedule([0] + list(stop_order), all_locations, time_matrix, start_min)
    return len(violations) * APPOINTMENT_VIOLATION_PENALTY_SEC


def recommended_departure(
    full_order: List[int],
    all_locations: List[LocationItem],
    time_matrix: List[List[int]],
) -> Tuple[Optional[int], Optional[bool]]:
    """확정된 방문 순서에서 '약속을 지킬 수 있는 가장 늦은 출발 시각'을 역산한다.

    build_schedule의 약속 위반 개수는 출발 시각이 늦어질수록 단조 증가한다
    (늦게 나가면 도착도 늦거나 같아지고, 약속 대기는 지연만 시키므로).
    따라서 '위반이 D=0에서의 최소치와 같은 최대 D'를 이진탐색으로 찾는다.
      - 모든 약속을 지킬 수 있으면(=최소 위반 0): 그 시각까지는 늦게 나가도 안전 -> feasible=True
      - 불가능하면(최소 위반>0): 위반을 최소로 유지하는 가장 늦은 출발 시각 -> feasible=False
    약속이 하나도 없으면 (None, None)을 반환한다(추천 없음)."""
    has_appt = any(
        parse_hhmm(getattr(all_locations[n], "appointment_time", None)) is not None
        for n in full_order
    )
    if not has_appt:
        return None, None

    def violation_count(dep_min: int) -> int:
        _, viol, _ = build_schedule(full_order, all_locations, time_matrix, dep_min)
        return len(viol)

    target = violation_count(0)  # 단조증가라 D=0이 위반 최소
    lo, hi = 0, 24 * 60 - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if violation_count(mid) == target:
            lo = mid
        else:
            hi = mid - 1
    return lo, (target == 0)


def haversine_distance_m(a: LocationItem, b: LocationItem) -> float:
    R = 6371000.0
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlng = math.radians(b.lng - a.lng)
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(h))


# =========================================================
# 1. 로컬 모델(Ollama) 장소 추출 + Kakao 지오코딩
# =========================================================

# backend/finetune/generate_dataset.py 의 TASK_INSTRUCTION, 그리고 파인튜닝 노트북의
# alpaca_prompt 와 정확히 동일해야 한다 (학습 때와 다른 형식으로 프롬프트를 주면 성능이 크게 떨어짐).
TASK_INSTRUCTION = """아래 일정 문장에서 방문해야 할 장소를 모두 추출해 JSON 배열로 반환해줘.

각 항목은 반드시 다음 필드를 가져야 한다.
- name: 장소명
- task: 그 장소에서 해야 할 일
- priority: 중요도 1~5, 1이 가장 중요함
- lat: 숫자
- lng: 숫자
- address: 알고 있다면 주소, 모르면 빈 문자열
- appointment_time: 그 장소에 도착해야 하는 약속/예약 시각. 문장에 명시적 시각이 있으면 "HH:MM"(24시간제) 문자열로, 없으면 null

시각은 오전/오후를 반영해 24시간제로 변환한다 (예: "오후 3시" -> "15:00", "밤 9시" -> "21:00"). 명시적 시각이 없으면 appointment_time은 null이다.
장소명이 애매하면 가장 유력한 장소명을 사용한다.
응답에는 JSON 배열만 포함한다."""

ALPACA_PROMPT = """다음은 작업을 설명하는 지시문과, 참고할 입력이 짝지어져 있습니다.
요청을 적절히 완료하는 응답을 작성하세요.

### 지시문:
{}

### 입력:
{}

### 응답:
{}"""


async def call_ollama(prompt: str) -> str:
    """Ollama에 등록된 파인튜닝 모델(routemate-parser)을 호출한다.

    raw=True: Modelfile의 TEMPLATE(채팅 래핑) 없이 prompt를 있는 그대로 모델에 전달한다.
    학습을 채팅 형식이 아니라 순수 텍스트 이어쓰기(alpaca_prompt) 형식으로 했기 때문에,
    추론 때도 반드시 이 형식을 그대로 맞춰야 한다.
    """
    url = f"{OLLAMA_HOST}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "raw": True,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()["response"]


async def geocode_place(name: str, client: httpx.AsyncClient) -> Optional[Dict[str, object]]:
    """카카오 로컬 키워드 검색으로 장소명 -> 실제 좌표/주소를 조회한다.

    LLM(Gemini든 파인튜닝한 로컬 모델이든)이 lat/lng를 직접 기억해서 답하는 건
    본질적으로 신뢰할 수 없다 (특히 소형 모델은 거의 항상 틀림). 그래서 LLM은
    장소명/할일/우선순위 추출까지만 맡기고, 좌표는 항상 이 함수로 다시 조회해서 덮어쓴다.
    """
    if not KAKAO_REST_API_KEY or not name:
        return None

    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
    params = {"query": name, "size": 1}

    try:
        response = await client.get(url, headers=headers, params=params, timeout=5)
        response.raise_for_status()
        documents = response.json().get("documents") or []
        if not documents:
            return None
        doc = documents[0]
        return {
            "lat": float(doc["y"]),
            "lng": float(doc["x"]),
            "address": doc.get("road_address_name") or doc.get("address_name") or "",
        }
    except Exception as e:
        print(f"Kakao geocoding failed for '{name}':", e)
        return None


async def geocode_locations(locations: List[dict]) -> None:
    """locations 리스트를 in-place로 geocode. 조회 실패 시 기존 값(LLM 추정치)을 그대로 둔다."""
    if not KAKAO_REST_API_KEY:
        return
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[geocode_place(loc["name"], client) for loc in locations])
    for loc, geo in zip(locations, results):
        if geo:
            loc["lat"] = geo["lat"]
            loc["lng"] = geo["lng"]
            loc["address"] = geo["address"] or loc.get("address", "")


@app.post("/api/parse-tasks")
async def parse_tasks(req: ParseRequest):
    prompt = ALPACA_PROMPT.format(TASK_INSTRUCTION, req.user_text, "")

    try:
        raw_text = await call_ollama(prompt)

        start = raw_text.find("[")
        end = raw_text.rfind("]")
        if start == -1 or end == -1:
            raise ValueError(f"응답에서 JSON 배열을 찾을 수 없습니다: {raw_text[:200]!r}")
        parsed = json.loads(raw_text[start : end + 1])

        if not isinstance(parsed, list):
            raise ValueError("모델 응답이 배열이 아닙니다.")

        normalized = []
        for item in parsed:
            # 모델이 뽑은 약속 시각은 "HH:MM"로 정규화하고, 형식이 어긋나면 버린다(None).
            appt_raw = item.get("appointment_time")
            appt = format_hhmm(parse_hhmm(appt_raw)) if appt_raw else None
            normalized.append(
                {
                    "name": str(item.get("name", "장소")),
                    "task": str(item.get("task", "방문")),
                    "priority": clamp_priority(item.get("priority", 3)),
                    "lat": float(item.get("lat", 0)),
                    "lng": float(item.get("lng", 0)),
                    "address": str(item.get("address", "")),
                    "appointment_time": appt,
                }
            )

        if not normalized:
            raise ValueError("장소가 추출되지 않았습니다.")

        # 모델이 뱉은 lat/lng/address는 신뢰하지 않고 Kakao로 다시 조회해서 덮어쓴다.
        await geocode_locations(normalized)

        return {"status": "success", "data": normalized, "is_mock": False}

    except Exception as e:
        print("로컬 모델(Ollama) 파싱 실패:", e)
        return {"status": "success", "data": mock_data(), "is_mock": True}


def mock_data():
    return [
        {"name": "강남역", "task": "중요 미팅", "priority": 1, "lat": 37.4979, "lng": 127.0276, "address": "서울 강남구 강남대로 396"},
        {"name": "홍대입구역", "task": "점심 약속", "priority": 2, "lat": 37.5575, "lng": 126.9245, "address": "서울 마포구 양화로 160"},
        {"name": "서울역", "task": "KTX 탑승", "priority": 3, "lat": 37.5547, "lng": 126.9707, "address": "서울 용산구 한강대로 405"},
    ]


# =========================================================
# 2. 실제 이동시간 조회
# =========================================================

def extract_car_path(route: dict) -> List[List[float]]:
    """Kakao Mobility 응답의 도로 좌표열을 [[lat, lng], ...] 로 펼친다.
    vertexes는 [x1, y1, x2, y2, ...] (x=lng, y=lat) 평면 배열이라 2개씩 끊어 뒤집는다."""
    path: List[List[float]] = []
    for section in route.get("sections", []):
        for road in section.get("roads", []):
            vs = road.get("vertexes", []) or []
            for k in range(0, len(vs) - 1, 2):
                path.append([vs[k + 1], vs[k]])  # [lat, lng]
    return path


async def get_car_leg(origin: LocationItem, dest: LocationItem, client: httpx.AsyncClient, include_path: bool = False):
    """Kakao Mobility 자동차 길찾기. 반환: (duration sec, distance m, path[[lat,lng]...]).
    include_path=False면 path는 빈 리스트(행렬 계산 등 좌표열이 불필요한 경우 파싱 생략)."""
    if not KAKAO_REST_API_KEY:
        raise RuntimeError("KAKAO_REST_API_KEY가 없습니다.")

    url = "https://apis-navi.kakaomobility.com/v1/directions"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
    params = {
        "origin": f"{origin.lng},{origin.lat}",
        "destination": f"{dest.lng},{dest.lat}",
        "priority": "RECOMMEND",
    }

    response = await client.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    if not data.get("routes"):
        raise RuntimeError("Kakao 경로 결과가 없습니다.")

    route0 = data["routes"][0]
    summary = route0["summary"]
    path = extract_car_path(route0) if include_path else []
    return float(summary["duration"]), float(summary["distance"]), path


async def get_walk_leg(origin: LocationItem, dest: LocationItem, client: httpx.AsyncClient, include_path: bool = False):
    """Tmap(SK) 보행자 경로안내. 반환: (duration sec, distance m, path[[lat,lng]...]).

    응답은 GeoJSON FeatureCollection이며,
      - 총거리/총시간은 첫 Point feature의 properties(totalDistance[m], totalTime[sec]),
      - 실제 인도 경로는 LineString feature들의 coordinates([경도,위도] 순)에 들어있다.
    """
    if not TMAP_APP_KEY:
        raise RuntimeError("TMAP_APP_KEY가 없습니다.")

    url = "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1"
    headers = {
        "appKey": TMAP_APP_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "startX": origin.lng,
        "startY": origin.lat,
        "endX": dest.lng,
        "endY": dest.lat,
        "startName": quote(origin.name or "출발"),
        "endName": quote(dest.name or "도착"),
    }

    response = await client.post(url, headers=headers, json=body, timeout=10)
    response.raise_for_status()
    data = response.json()

    features = data.get("features") or []
    total_distance = None
    total_time = None
    path: List[List[float]] = []
    for f in features:
        props = f.get("properties") or {}
        if total_distance is None and props.get("totalDistance") is not None:
            total_distance = float(props["totalDistance"])
            total_time = float(props.get("totalTime") or 0)
        geom = f.get("geometry") or {}
        if include_path and geom.get("type") == "LineString":
            for coord in geom.get("coordinates") or []:
                # coord = [경도(lng), 위도(lat)] -> [lat, lng]
                path.append([coord[1], coord[0]])

    if total_distance is None:
        raise RuntimeError("Tmap 보행자 경로 응답에 총 거리 정보가 없습니다.")
    return float(total_time or 0), total_distance, path


def is_estimated_mode(mode: str) -> bool:
    """해당 이동수단의 거리/시간이 '실제 API'가 아니라 보정 추정값인지 여부.
    car: Kakao 키 없으면 추정 / walk: Tmap 키 없으면 추정 / transit: 아직 항상 추정."""
    if mode == "car":
        return not KAKAO_REST_API_KEY
    if mode == "walk":
        return not TMAP_APP_KEY
    return True  # transit 등은 아직 실API 미연동


def fallback_leg(origin: LocationItem, dest: LocationItem, mode: str):
    """자동차 API 실패 또는 도보/대중교통 임시 계산.
    신규 카카오맵 도보/대중교통 API(2026.07 오픈) 스펙 확정되면 실API로 교체할 것."""
    straight_m = haversine_distance_m(origin, dest)

    if mode == "walk":
        road_factor, speed_kmh = 1.20, 4.5
    elif mode == "transit":
        road_factor, speed_kmh = 1.40, 18.0
    else:
        road_factor, speed_kmh = 1.35, 25.0

    distance_m = straight_m * road_factor
    duration_sec = (distance_m / 1000) / speed_kmh * 3600

    if mode == "transit":
        duration_sec += 5 * 60

    # 폴백(도보/대중교통/자동차 API 실패)은 실제 도로 좌표열이 없으므로 path는 빈 리스트.
    return duration_sec, distance_m, []


async def get_leg_duration(origin: LocationItem, dest: LocationItem, mode: str, client: httpx.AsyncClient, include_path: bool = False):
    """반환: (duration sec, distance m, path). path는 자동차 실경로가 있을 때만 채워진다."""
    if mode == "car" and KAKAO_REST_API_KEY:
        try:
            return await get_car_leg(origin, dest, client, include_path=include_path)
        except Exception as e:
            print(f"Kakao 자동차 API 실패: {origin.name} -> {dest.name}: {e}")

    if mode == "walk" and TMAP_APP_KEY:
        try:
            return await get_walk_leg(origin, dest, client, include_path=include_path)
        except Exception as e:
            print(f"Tmap 보행자 API 실패: {origin.name} -> {dest.name}: {e}")

    return fallback_leg(origin, dest, mode)


# 최종 경로(route-eta) 전용 leg 캐시.
# 같은 좌표쌍+이동수단의 실 API 결과(거리/시간/경로 좌표)를 재사용해, 이동수단 토글이나
# 동일 장소 재최적화 때 무료 쿼터를 다시 태우지 않는다.
#  - 크기 상한(LRU): 상시 가동 시 무한 증가하지 않도록 오래된 항목부터 제거.
#  - TTL: 자동차/대중교통은 실시간성이 있어 낡은 값 재사용을 막는다. 도보는 거리 불변이라 무기한.
_FINAL_LEG_CACHE: "OrderedDict[Tuple, Tuple[Optional[float], Tuple[float, float, List[List[float]]]]]" = OrderedDict()
_FINAL_LEG_CACHE_MAX = 512
_FINAL_LEG_TTL_SEC: Dict[str, int] = {"car": 600, "transit": 600}  # walk 미지정 = 무기한


def _final_leg_key(origin: LocationItem, dest: LocationItem, mode: str) -> Tuple:
    return (mode, round(origin.lat, 6), round(origin.lng, 6), round(dest.lat, 6), round(dest.lng, 6))


async def get_final_leg(origin: LocationItem, dest: LocationItem, mode: str, client: httpx.AsyncClient):
    """최종 경로용: 실제 경로(path 포함)를 구하되 결과를 캐시한다.

    실 API가 실제 좌표열(path)을 준 경우에만 캐시한다. 키가 없거나 일시적 실패로
    추정 폴백된 결과(path 없음)는 무료이기도 하고, 굳혀두면 이후 실측 기회를 막으므로
    캐시하지 않는다.
    """
    key = _final_leg_key(origin, dest, mode)
    entry = _FINAL_LEG_CACHE.get(key)
    if entry is not None:
        expires_at, value = entry
        if expires_at is None or expires_at > time.monotonic():
            _FINAL_LEG_CACHE.move_to_end(key)  # LRU: 최근 사용으로 갱신
            return value
        del _FINAL_LEG_CACHE[key]  # 만료 → 제거 후 재조회

    duration, distance, path = await get_leg_duration(origin, dest, mode, client, include_path=True)
    if path:
        ttl = _FINAL_LEG_TTL_SEC.get(mode)
        expires_at = (time.monotonic() + ttl) if ttl else None
        _FINAL_LEG_CACHE[key] = (expires_at, (duration, distance, path))
        _FINAL_LEG_CACHE.move_to_end(key)
        while len(_FINAL_LEG_CACHE) > _FINAL_LEG_CACHE_MAX:
            _FINAL_LEG_CACHE.popitem(last=False)  # 가장 오래 안 쓴 항목 제거
    return duration, distance, path


async def build_time_distance_matrix(locations: List[LocationItem], travel_mode: str):
    """모든 장소 쌍의 이동시간/거리 행렬(방문 순서 최적화용).

    순서 결정에는 직선거리 기반 '추정치'만 사용한다. 실제 도로/인도/대중교통 API를
    쓰면 호출이 O(n^2)로 폭증해(무료 쿼터가 매우 적은 Tmap/대중교통은 데모 1회도
    못 돌릴 정도) 쿼터를 금방 소진한다. 반면 2~5곳 규모에서는 추정만으로도 방문
    순서가 실제와 거의 동일하다. 정확한 거리/시간과 지도에 그릴 실제 경로는, 확정된
    '최종 경로'에 대해서만 /api/route-eta가 실 API(n-1회)로 계산한다.
    """
    n = len(locations)
    time_matrix = [[0] * n for _ in range(n)]
    distance_matrix = [[0] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            duration, distance, _ = fallback_leg(locations[i], locations[j], travel_mode)
            time_matrix[i][j] = max(1, int(round(duration)))
            distance_matrix[i][j] = max(1, int(round(distance)))

    # 행렬 자체는 항상 추정치지만, 사용자에게 표시할 '추정' 여부는 최종 경로 기준으로
    # 판단한다(실 API가 있는 모드면 최종 경로는 실측되므로 estimated=False).
    estimated = is_estimated_mode(travel_mode)
    return time_matrix, distance_matrix, estimated


# =========================================================
# 3. 세 가지 최적화 모드 (핵심 수정 사항)
# =========================================================
#
# 모든 모드는 "출발지(index 0)는 고정, 나머지 목적지들의 방문 순서만 결정"하는
# open-path 문제로 취급한다. time_matrix는 build_time_distance_matrix()로 미리 구해둔
# n x n 행렬(초 단위)을 그대로 쓴다.


def path_travel_time(order: List[int], time_matrix: List[List[int]]) -> int:
    """0번(출발지)에서 시작해 order 순서대로 방문할 때의 총 이동시간(초)."""
    total = 0
    cur = 0
    for node in order:
        total += time_matrix[cur][node]
        cur = node
    return total


def solve_shortest(
    stop_indices: List[int],
    time_matrix: List[List[int]],
    all_locations: Optional[List[LocationItem]] = None,
    start_min: Optional[int] = None,
) -> List[int]:
    """순수 이동시간 최소화. 우선순위는 완전히 무시한다.
    단, 출발 시각이 주어지면 약속 시각을 어기는 순서에는 큰 페널티를 얹어
    '약속을 지키는 순서'가 먼저 선택되도록 한다."""
    if len(stop_indices) <= MAX_BRUTE_FORCE_STOPS:
        best_order, best_cost = None, None
        for perm in itertools.permutations(stop_indices):
            cost = path_travel_time(list(perm), time_matrix)
            if all_locations is not None:
                cost += appointment_penalty_sec(list(perm), all_locations, time_matrix, start_min)
            if best_cost is None or cost < best_cost:
                best_cost, best_order = cost, list(perm)
        return best_order

    # 장소가 너무 많을 때만 OR-Tools로 근사 (순수 이동시간 최소화 목적)
    return solve_with_ortools(stop_indices, time_matrix, cost_fn=None)


def solve_priority(stop_indices: List[int], time_matrix: List[List[int]], locations: List[LocationItem]) -> List[int]:
    """
    우선순위 그룹 순서를 절대 기준으로 강제한다.
    - priority 값이 작은(중요한) 그룹은 반드시 큰 그룹보다 먼저 전부 방문된다.
    - 같은 그룹 안에서는 이동시간이 최소가 되는 순서를 고른다.
    DP(bucket, end_node) -> (cost, path) 로 정확히 계산한다.
    """
    buckets: Dict[int, List[int]] = {}
    for idx in stop_indices:
        p = clamp_priority(locations[idx].priority)
        buckets.setdefault(p, []).append(idx)

    ordered_bucket_keys = sorted(buckets.keys())  # 1(최우선) -> 5(여유)

    # dp: {end_node: (cost, path_so_far)}  시작은 출발지(0)에서 cost 0
    dp: Dict[int, Tuple[int, List[int]]] = {0: (0, [])}

    for key in ordered_bucket_keys:
        bucket_nodes = buckets[key]
        new_dp: Dict[int, Tuple[int, List[int]]] = {}

        for prev_end, (prev_cost, prev_path) in dp.items():
            # 이 그룹 내부의 모든 방문 순서를 다 시도 (그룹 크기는 보통 작음)
            for perm in itertools.permutations(bucket_nodes):
                cost = prev_cost
                cur = prev_end
                for node in perm:
                    cost += time_matrix[cur][node]
                    cur = node
                end_node = cur
                if end_node not in new_dp or cost < new_dp[end_node][0]:
                    new_dp[end_node] = (cost, prev_path + list(perm))

        dp = new_dp

    # 마지막 그룹까지 처리한 뒤, 비용이 최소인 경로 선택
    best_end = min(dp, key=lambda k: dp[k][0])
    return dp[best_end][1]


def solve_ai(
    stop_indices: List[int],
    time_matrix: List[List[int]],
    locations: List[LocationItem],
    start_min: Optional[int] = None,
) -> List[int]:
    """
    이동시간 + (중요도 x 방문 순서 위치) 페널티를 합산한 점수를 최소화.
    중요한 장소(priority 숫자가 작음)일수록 "늦게 방문할 때" 페널티가 커지도록
    importance_score(= 6 - priority)를 가중치로 곱한다.
    순서 자체가 점수에 실질적으로 반영된다 (기존 버그였던 "순서 무관 총합 동일" 문제 해결).

    출발 시각이 주어지면 약속 시각 위반 페널티를 더해, 약속을 지키는 순서를
    (우선순위/이동시간보다 우선해서) 먼저 선택하도록 한다.
    """
    if len(stop_indices) <= MAX_BRUTE_FORCE_STOPS:
        best_order, best_score = None, None
        for perm in itertools.permutations(stop_indices):
            travel = path_travel_time(list(perm), time_matrix)
            penalty = sum(
                importance_score(locations[node].priority) * position * AI_PRIORITY_WEIGHT_SEC
                for position, node in enumerate(perm)
            )
            score = travel + penalty + appointment_penalty_sec(list(perm), locations, time_matrix, start_min)
            if best_score is None or score < best_score:
                best_score, best_order = score, list(perm)
        return best_order

    # 장소가 많을 때는 OR-Tools에 "다음 노드의 중요도 x 대략적 순번" 근사 비용을 실어 근사한다.
    def cost_fn(from_node, to_node, position):
        return time_matrix[from_node][to_node] + importance_score(locations[to_node].priority) * position * AI_PRIORITY_WEIGHT_SEC

    return solve_with_ortools(stop_indices, time_matrix, cost_fn=cost_fn)


def solve_with_ortools(stop_indices: List[int], time_matrix: List[List[int]], cost_fn=None) -> List[int]:
    """
    장소 수가 MAX_BRUTE_FORCE_STOPS를 초과하는 예외적인 경우의 근사 해법.
    cost_fn이 없으면 순수 이동시간 최소화, 있으면 해당 비용함수를 사용.
    (OR-Tools 표준 API 특성상 position 인자는 정확한 전역 순번이 아닌 근사치로만 반영됨)
    """
    node_list = [0] + stop_indices  # 0=출발지를 다시 포함시켜 로컬 인덱스 구성
    n = len(node_list)
    local_time = [[time_matrix[node_list[i]][node_list[j]] for j in range(n)] for i in range(n)]

    manager = pywrapcp.RoutingIndexManager(n, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def transit_callback(from_index, to_index):
        i, j = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        if i == j:
            return 0
        if cost_fn is None:
            return local_time[i][j]
        # position 근사치로 to-node의 로컬 인덱스를 사용 (완전 정확한 전역 순번은 아님)
        return cost_fn(node_list[i], node_list[j], j)

    callback_index = routing.RegisterTransitCallback(transit_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(callback_index)

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.seconds = 5

    solution = routing.SolveWithParameters(parameters)
    if solution is None:
        return stop_indices  # 최후의 폴백: 원래 순서 그대로

    order = []
    index = routing.Start(0)
    index = solution.Value(routing.NextVar(index))  # 출발지(0) 다음부터
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        order.append(node_list[node])
        index = solution.Value(routing.NextVar(index))

    return order


# =========================================================
# 4. /api/optimize-route
# =========================================================

@app.post("/api/optimize-route")
async def optimize_route(req: OptimizeRequest):
    all_locations = [req.start_location] + req.locations

    if len(all_locations) <= 1:
        return {"status": "success", "optimized_locations": [loc.model_dump() for loc in all_locations]}

    validate_locations(all_locations)

    if req.optimize_mode not in {"ai", "shortest", "priority"}:
        raise HTTPException(status_code=400, detail="optimize_mode는 ai, shortest, priority 중 하나여야 합니다.")

    time_matrix, distance_matrix, estimated = await build_time_distance_matrix(all_locations, req.travel_mode)

    start_min = parse_hhmm(req.start_time)
    stop_indices = list(range(1, len(all_locations)))  # 0은 출발지, 나머지가 목적지

    if req.optimize_mode == "shortest":
        order = solve_shortest(stop_indices, time_matrix, all_locations, start_min)
    elif req.optimize_mode == "priority":
        order = solve_priority(stop_indices, time_matrix, all_locations)
    else:  # "ai"
        order = solve_ai(stop_indices, time_matrix, all_locations, start_min)

    optimized_locations = [all_locations[0]] + [all_locations[i] for i in order]

    # 확정된 순서에 대해 시간축을 계산해, 약속 위반 여부를 함께 알려준다.
    # (여기 time_matrix는 추정치일 수 있으므로 정확한 도착 시각은 /api/route-eta가 계산한다)
    _, violations, _ = build_schedule([0] + order, all_locations, time_matrix, start_min)
    violated_names = [all_locations[i].name for i in violations]

    return {
        "status": "success",
        "optimized_locations": [loc.model_dump() for loc in optimized_locations],
        "optimize_mode": req.optimize_mode,
        "travel_mode": req.travel_mode,
        "estimated": estimated,
        "appointment_violations": violated_names,
    }


# =========================================================
# 5. 최종 경로의 실제 거리 / 시간 계산
# =========================================================

@app.post("/api/route-eta")
async def calculate_route_eta(req: RouteDetailRequest):
    locs = req.ordered_locations

    if len(locs) < 2:
        return {"status": "success", "total_duration_min": 0, "total_distance_km": 0, "legs": [], "estimated": False}

    validate_locations(locs)

    total_duration = 0.0
    total_distance = 0.0
    legs = []
    estimated = is_estimated_mode(req.travel_mode)

    leg_secs: List[float] = []

    async with httpx.AsyncClient() as client:
        for i in range(len(locs) - 1):
            origin, dest = locs[i], locs[i + 1]
            # 최종 경로이므로 실제 도로/인도 좌표열(path)까지 받아 지도에 그리게 한다.
            # get_final_leg는 결과를 캐시해 같은 구간 재요청 시 쿼터를 아낀다.
            duration, distance, path = await get_final_leg(
                origin, dest, req.travel_mode, client
            )
            total_duration += duration
            total_distance += distance
            leg_secs.append(duration)
            legs.append({
                "from": origin.name,
                "to": dest.name,
                "duration_min": round(duration / 60, 1),
                "distance_km": round(distance / 1000, 2),
                # 자동차 실경로가 있으면 [[lat,lng],...], 도보/대중교통(폴백)이면 빈 리스트.
                "path": path,
            })

    # 연속 구간 이동시간으로 시간행렬을 구성한다(스케줄/역산 계산에 재사용).
    n = len(locs)
    time_matrix = [[0] * n for _ in range(n)]
    for i in range(n - 1):
        time_matrix[i][i + 1] = max(1, int(round(leg_secs[i])))
    full_order = list(range(n))

    # 유효 출발 시각 결정:
    #  - 사용자가 지정했으면 그 시각.
    #  - 미지정(③)인데 약속이 있으면 '약속을 지킬 수 있는 가장 늦은 출발 시각'을 역산해 사용.
    #  - 미지정 + 약속 없음이면 시간축 계산을 하지 않는다.
    start_min = parse_hhmm(req.start_time)
    recommended_min: Optional[int] = None
    recommended_feasible: Optional[bool] = None
    if start_min is not None:
        eff_start: Optional[int] = start_min
    else:
        eff_start, recommended_feasible = recommended_departure(full_order, locs, time_matrix)
        recommended_min = eff_start  # 역산값(약속 없으면 None)

    result = {
        "status": "success",
        "total_duration_min": round(total_duration / 60),
        "total_distance_km": round(total_distance / 1000, 1),
        "legs": legs,
        "estimated": estimated,
    }

    # 유효 출발 시각이 있으면 시간축(도착/출발/종료·위반)을 계산해 붙인다.
    if eff_start is not None:
        schedule, viol_nodes, finish = build_schedule(full_order, locs, time_matrix, eff_start)
        stops = [
            {
                "name": locs[s["node"]].name,
                "arrival_time": format_hhmm(s["arrival_min"]),
                "depart_time": format_hhmm(s["depart_min"]),
                "appointment_time": (
                    getattr(locs[s["node"]], "appointment_time", None) if s["node"] != 0 else None
                ),
                "late": s["late"],
            }
            for s in schedule
        ]
        result["start_time"] = format_hhmm(eff_start)
        result["finish_time"] = format_hhmm(finish)
        result["total_elapsed_min"] = round(finish - eff_start) if finish is not None else None
        result["stops"] = stops
        result["appointment_violations"] = [locs[nd].name for nd in viol_nodes]
        # 역산으로 출발 시각을 정한 경우, '추천 출발 시각'임을 함께 알린다.
        if recommended_min is not None:
            result["recommended_start_time"] = format_hhmm(recommended_min)
            result["recommended_feasible"] = recommended_feasible

    return result


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "ollama_host": OLLAMA_HOST,
        "ollama_model": OLLAMA_MODEL,
        "kakao_rest_configured": bool(KAKAO_REST_API_KEY),
        "tmap_configured": bool(TMAP_APP_KEY),
    }
