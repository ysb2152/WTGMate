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

# 파인튜닝해서 Ollama에 올린 로컬 모델 (원래 Gemini 쓰던 걸 대체함)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "wtgmate-parser")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
# 도보(Tmap) / 대중교통(ODsay) 키. 없으면 fallback_leg 추정으로 넘어감.
TMAP_APP_KEY = os.getenv("TMAP_APP_KEY")
ODSAY_API_KEY = os.getenv("ODSAY_API_KEY")  # 무료 30회/일이라 아껴 써야 함

# 배포 도메인 콤마로 넣기. 안 넣으면 로컬 개발용으로 전부 허용.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]


app = FastAPI(title="WTGMate API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # 쿠키 안 쓰니까 False. ("*" + credentials=True는 브라우저가 막음)
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 튜닝 상수들

# 완전탐색 돌릴 최대 목적지 수. 9!=36만이라 그냥 돌려도 1초 안에 끝남. 넘으면 OR-Tools로.
MAX_BRUTE_FORCE_STOPS = 9

# AI 모드에서 우선순위를 이동시간(초)이랑 같이 계산하려고 곱하는 가중치.
# priority가 1이 제일 중요라 그대로 곱하면 방향이 반대라 6 - priority로 뒤집어 씀.
# 값 몇 개 넣어보니 너무 작으면 우선순위가 안 먹고 너무 크면 priority 모드랑 똑같아져서 100쯤.
AI_PRIORITY_WEIGHT_SEC = 100

# 약속시각 어겼을 때 페널티. 이동시간/우선순위보다 압도적으로 크게 잡아서 사실상 하드 제약처럼 동작.
APPOINTMENT_VIOLATION_PENALTY_SEC = 10_000_000

# 허용 지각(분). 0이면 약속시각까지 딱 맞춰 도착해야 함.
APPOINTMENT_TOLERANCE_MIN = 0


# 요청/응답 모델

class ParseRequest(BaseModel):
    user_text: str


class LocationItem(BaseModel):
    name: str
    task: str
    priority: Optional[int] = 3
    lat: float
    lng: float
    address: Optional[str] = ""
    # "HH:MM" 약속시각(도착 마감). LLM이 문장에서 뽑음. 있으면 그 시각까지 도착해야 하는 하드 제약.
    appointment_time: Optional[str] = None
    duration_min: Optional[int] = 0  # 체류 시간(분), 사용자 입력


class OptimizeRequest(BaseModel):
    start_location: LocationItem
    locations: List[LocationItem]
    travel_mode: str = "car"
    # shortest : 우선순위 완전 무시, 순수 이동시간 최소화
    # priority : 우선순위 그룹 순서를 절대 기준으로 강제, 그룹 내에서만 이동시간 최소화
    # ai       : 이동시간 + (우선순위 x 방문순서) 페널티를 종합한 점수 최소화
    optimize_mode: str = "ai"
    # "HH:MM" 출발 예정 시각. '현재 시각'이 아니라 사용자가 정한 값(내일일 수도 있음).
    # 있으면 약속 제약/도착시각 계산에 씀.
    start_time: Optional[str] = None


class RouteDetailRequest(BaseModel):
    ordered_locations: List[LocationItem]
    travel_mode: str = "car"
    # 있으면 각 장소 도착/출발 시각을 계산해서 같이 돌려줌.
    start_time: Optional[str] = None


# 공통 헬퍼

def clamp_priority(value: Optional[int]) -> int:
    try:
        # value=0이 `value or 3`에서 falsy로 걸려 3이 되던 버그가 있어서, None만 기본값 처리.
        value = int(value) if value is not None else 3
    except (TypeError, ValueError):
        value = 3
    return max(1, min(5, value))


def importance_score(priority: Optional[int]) -> int:
    # priority(1=중요~5=여유)를 가중치로 뒤집음. 1->5, 5->1
    return 6 - clamp_priority(priority)


def validate_locations(locations: List[LocationItem]):
    for index, loc in enumerate(locations):
        if not math.isfinite(loc.lat) or not math.isfinite(loc.lng):
            raise HTTPException(
                status_code=400,
                detail=f"{index}번째 장소 [{loc.name}]의 좌표가 올바르지 않습니다.",
            )


def parse_hhmm(value: Optional[str]) -> Optional[int]:
    """'HH:MM' -> 자정 기준 분. 형식 이상하면 None."""
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
    """분 -> 'HH:MM'. 자정 넘으면 24로 나눈 나머지(같은 날 취급)."""
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
    """방문 순서(0=출발지 포함)대로 시간축을 계산.

    도착 = 직전 출발 + 이동시간. 약속시각이 있으면 일찍 오면 그 시각까지 대기,
    늦으면 위반으로 기록. 그다음 체류시간만큼 있다가 출발.

    반환: (schedule, violations, finish_min)
      finish_min = 마지막 장소에서의 출발 시각(=일정 끝나는 시각)
    """
    schedule = []
    violations = []
    t: Optional[float] = start_min

    for pos, node in enumerate(full_order):
        appt = parse_hhmm(getattr(all_locations[node], "appointment_time", None))
        late = False

        if pos == 0:
            arrival = t  # 출발지는 도착=출발
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
                    t = appt  # 일찍 왔으면 약속시각까지 대기

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
    """출발시각을 모를 때 쓰는 페널티. 도착시각을 못 구하니까,
    약속 있는 장소들끼리 '이른 약속 -> 늦은 약속' 순서가 되도록
    뒤집힌 쌍 하나당 큰 페널티. (약속 0~1개면 0)"""
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
    """방문 순서의 약속 페널티(초).
    출발시각 있으면 실제 지각 개수로, 없으면 시간순 정렬만 유도."""
    if start_min is None:
        return chronological_penalty_sec(stop_order, all_locations)
    _, violations, _ = build_schedule([0] + list(stop_order), all_locations, time_matrix, start_min)
    return len(violations) * APPOINTMENT_VIOLATION_PENALTY_SEC


def recommended_departure(
    full_order: List[int],
    all_locations: List[LocationItem],
    time_matrix: List[List[int]],
) -> Tuple[Optional[int], Optional[bool]]:
    """확정된 순서에서 '약속 지킬 수 있는 가장 늦은 출발시각'을 역산.

    위반 개수는 출발이 늦어질수록 단조 증가(늦게 나가면 도착도 늦어짐)라서,
    위반이 D=0일 때 최소치와 같은 가장 큰 D를 이진탐색으로 찾음.
      전부 지킬 수 있으면 feasible=True, 애초에 불가능하면 위반 최소인 최대 시각 + False.
    약속이 하나도 없으면 (None, None)."""
    has_appt = any(
        parse_hhmm(getattr(all_locations[n], "appointment_time", None)) is not None
        for n in full_order
    )
    if not has_appt:
        return None, None

    def violation_count(dep_min: int) -> int:
        _, viol, _ = build_schedule(full_order, all_locations, time_matrix, dep_min)
        return len(viol)

    target = violation_count(0)  # 단조증가라 0시 출발이 위반 최소
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


# 장소 추출(Ollama) + Kakao 지오코딩

# 이 지시문은 generate_dataset.py랑 파인튜닝 노트북(alpaca_prompt)이랑 글자까지 똑같이 맞춰야 함.
# 학습 때랑 다른 형식으로 넣으면 소형 모델이라 성능 확 떨어짐.
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
    """파인튜닝 모델(wtgmate-parser) 호출.

    raw=True로 Modelfile 채팅 래핑 없이 프롬프트를 그대로 넣는다.
    학습을 채팅형이 아니라 텍스트 이어쓰기(alpaca_prompt)로 해서 추론도 같은 형식이어야 함.
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
    """카카오 로컬 키워드 검색으로 장소명 -> 실제 좌표/주소.

    LLM이 뱉은 lat/lng는 못 믿는다(소형 모델은 거의 다 틀림). LLM은 장소명/할일/우선순위까지만
    맡기고 좌표는 여기서 다시 조회해서 덮어쓴다.
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
    """locations를 in-place로 지오코딩. 실패하면 LLM이 준 값 그대로 둠."""
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
            # 약속시각은 "HH:MM"로 정규화, 형식 이상하면 버림.
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

        # 좌표는 못 믿으니 Kakao로 다시 조회해서 덮어씀.
        await geocode_locations(normalized)

        return {"status": "success", "data": normalized, "is_mock": False}

    except Exception as e:
        # 파싱 실패해도(콜드스타트 등) 데모 안 끊기게 목업으로 폴백.
        print("로컬 모델(Ollama) 파싱 실패:", e)
        return {"status": "success", "data": mock_data(), "is_mock": True}


def mock_data():
    return [
        {"name": "강남역", "task": "중요 미팅", "priority": 1, "lat": 37.4979, "lng": 127.0276, "address": "서울 강남구 강남대로 396"},
        {"name": "홍대입구역", "task": "점심 약속", "priority": 2, "lat": 37.5575, "lng": 126.9245, "address": "서울 마포구 양화로 160"},
        {"name": "서울역", "task": "KTX 탑승", "priority": 3, "lat": 37.5547, "lng": 126.9707, "address": "서울 용산구 한강대로 405"},
    ]


# 실제 이동시간 조회

def extract_car_path(route: dict) -> List[List[float]]:
    """Kakao Mobility 응답의 도로 좌표를 [[lat, lng], ...]로 펼침.
    vertexes가 [x1,y1,x2,y2,...] (x=경도, y=위도)라 2개씩 끊어서 뒤집음."""
    path: List[List[float]] = []
    for section in route.get("sections", []):
        for road in section.get("roads", []):
            vs = road.get("vertexes", []) or []
            for k in range(0, len(vs) - 1, 2):
                path.append([vs[k + 1], vs[k]])  # [lat, lng]
    return path


async def get_car_leg(origin: LocationItem, dest: LocationItem, client: httpx.AsyncClient, include_path: bool = False):
    """Kakao Mobility 자동차 길찾기. 반환: (초, 미터, path).
    include_path=False면 좌표 파싱 생략(빈 리스트)."""
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
    # Kakao는 실패해도 200에 result_code만 담아 보냄(예: 104=출발/도착 너무 가까움).
    # 이때 summary가 없어서 예전엔 KeyError로 터졌음. result_code(0=성공) 먼저 확인.
    # 실패하면 위에서 추정 폴백되는데, 이런 건 대개 짧은 구간이라 추정으로 충분함.
    result_code = route0.get("result_code", 0)
    if result_code != 0 or "summary" not in route0:
        raise RuntimeError(f"Kakao 경로 실패(code {result_code}): {route0.get('result_msg', 'summary 없음')}")

    summary = route0["summary"]
    path = extract_car_path(route0) if include_path else []
    return float(summary["duration"]), float(summary["distance"]), path


async def get_walk_leg(origin: LocationItem, dest: LocationItem, client: httpx.AsyncClient, include_path: bool = False):
    """Tmap 보행자 경로. 반환: (초, 미터, path).

    응답이 GeoJSON이라 총거리/시간은 첫 Point의 properties에서,
    실제 인도 경로는 LineString들의 coordinates([경도,위도])에서 뽑음.
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
                # [경도, 위도] -> [위도, 경도]
                path.append([coord[1], coord[0]])

    if total_distance is None:
        raise RuntimeError("Tmap 보행자 경로 응답에 총 거리 정보가 없습니다.")
    return float(total_time or 0), total_distance, path


async def get_transit_leg(origin: LocationItem, dest: LocationItem, client: httpx.AsyncClient, include_path: bool = False):
    """ODsay 대중교통 길찾기(searchPubTransPathT). 반환: (초, 미터, path).

    result.path[] 중 첫 경로(최적)만 사용.
      총시간 = info.totalTime(분), 총거리 = subPath distance 합.
      경로는 각 subPath의 passStopList.stations(정류장 좌표)로 그림.
      정류장 좌표 기반이라 loadLane 2차 호출 없이 1번으로 끝내서 쿼터 아낌.
    """
    if not ODSAY_API_KEY:
        raise RuntimeError("ODSAY_API_KEY가 없습니다.")

    url = "https://api.odsay.com/v1/api/searchPubTransPathT"
    # apiKey는 httpx가 알아서 URL 인코딩해줌.
    params = {
        "SX": origin.lng,
        "SY": origin.lat,
        "EX": dest.lng,
        "EY": dest.lat,
        "apiKey": ODSAY_API_KEY,
    }

    response = await client.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    # ODsay도 실패 시 200에 error 담아 보냄(예: 너무 가까워서 도보 권장).
    if data.get("error"):
        raise RuntimeError(f"ODsay 오류: {data.get('error')}")

    paths = ((data.get("result") or {}).get("path")) or []
    if not paths:
        raise RuntimeError("ODsay 대중교통 경로가 없습니다.")

    best = paths[0]
    info = best.get("info") or {}
    total_time_min = info.get("totalTime")
    if total_time_min is None:
        raise RuntimeError("ODsay 응답에 totalTime이 없습니다.")

    total_distance = 0.0
    path: List[List[float]] = []
    for sp in best.get("subPath") or []:
        d = sp.get("distance")
        if d:
            total_distance += float(d)
        if include_path:
            stations = ((sp.get("passStopList") or {}).get("stations")) or []
            for st in stations:
                x, y = st.get("x"), st.get("y")
                if x is not None and y is not None:
                    path.append([float(y), float(x)])  # [위도, 경도]

    # 도보 환승만 있어서 정류장 좌표가 없으면 최소한 직선이라도 그림.
    if include_path and len(path) < 2:
        path = [[origin.lat, origin.lng], [dest.lat, dest.lng]]

    # 거리 정보 없으면 직선거리로 보정(시간은 실측값 씀).
    if total_distance <= 0:
        total_distance = haversine_distance_m(origin, dest)

    return float(total_time_min) * 60.0, total_distance, path


def is_estimated_mode(mode: str) -> bool:
    """이 이동수단이 실 API 없이 추정으로 도는 중인지. (해당 키가 없으면 추정)"""
    if mode == "car":
        return not KAKAO_REST_API_KEY
    if mode == "walk":
        return not TMAP_APP_KEY
    if mode == "transit":
        return not ODSAY_API_KEY
    return True


def fallback_leg(origin: LocationItem, dest: LocationItem, mode: str):
    """실 API 없거나 실패했을 때 추정 계산. 직선거리 x 도로보정 x 모드별 속도.
    실 API가 정상이면 여기 안 옴."""
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
        duration_sec += 5 * 60  # 대기/환승 여유

    # 추정이라 실제 좌표는 없음. path는 빈 리스트.
    return duration_sec, distance_m, []


async def get_leg_duration(origin: LocationItem, dest: LocationItem, mode: str, client: httpx.AsyncClient, include_path: bool = False):
    """반환: (초, 미터, path). 이동수단별로 실 API 시도하고, 실패하면 조용히 추정 폴백."""
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

    if mode == "transit" and ODSAY_API_KEY:
        try:
            return await get_transit_leg(origin, dest, client, include_path=include_path)
        except Exception as e:
            print(f"ODsay 대중교통 API 실패: {origin.name} -> {dest.name}: {e}")

    return fallback_leg(origin, dest, mode)


# 최종 경로(route-eta)용 leg 캐시.
# 같은 좌표쌍+이동수단 결과를 재사용해서, 이동수단 토글이나 재최적화 때 쿼터 안 태움.
# LRU로 크기 제한(상시 가동 시 무한 증가 방지), TTL로 실시간성 있는 자동차/대중교통은 만료시킴(도보는 무기한).
_FINAL_LEG_CACHE: "OrderedDict[Tuple, Tuple[Optional[float], Tuple[float, float, List[List[float]]]]]" = OrderedDict()
_FINAL_LEG_CACHE_MAX = 512
_FINAL_LEG_TTL_SEC: Dict[str, int] = {"car": 600, "transit": 600}  # walk는 무기한


def _now() -> float:
    """TTL 판정용 시계. time.monotonic을 직접 패치하면 asyncio 루프 시계까지 바뀌어서
    테스트에서 이것만 갈아끼우려고 한 겹 뺐음."""
    return time.monotonic()


def _final_leg_key(origin: LocationItem, dest: LocationItem, mode: str) -> Tuple:
    return (mode, round(origin.lat, 6), round(origin.lng, 6), round(dest.lat, 6), round(dest.lng, 6))


async def get_final_leg(origin: LocationItem, dest: LocationItem, mode: str, client: httpx.AsyncClient):
    """최종 경로용: 실제 경로(path 포함)를 구하고 캐시.

    실 API가 좌표(path)를 준 경우에만 캐시. 추정 폴백된 건(path 없음) 무료이기도 하고
    굳혀두면 다음에 실측할 기회를 막아서 캐시 안 함.
    """
    key = _final_leg_key(origin, dest, mode)
    entry = _FINAL_LEG_CACHE.get(key)
    if entry is not None:
        expires_at, value = entry
        if expires_at is None or expires_at > _now():
            _FINAL_LEG_CACHE.move_to_end(key)  # 최근 사용으로 갱신
            return value
        del _FINAL_LEG_CACHE[key]  # 만료됐으면 지우고 다시 조회

    duration, distance, path = await get_leg_duration(origin, dest, mode, client, include_path=True)
    if path:
        ttl = _FINAL_LEG_TTL_SEC.get(mode)
        expires_at = (_now() + ttl) if ttl else None
        _FINAL_LEG_CACHE[key] = (expires_at, (duration, distance, path))
        _FINAL_LEG_CACHE.move_to_end(key)
        while len(_FINAL_LEG_CACHE) > _FINAL_LEG_CACHE_MAX:
            _FINAL_LEG_CACHE.popitem(last=False)  # 오래된 것부터 버림
    return duration, distance, path


async def build_time_distance_matrix(locations: List[LocationItem], travel_mode: str):
    """모든 장소 쌍의 이동시간/거리 행렬(순서 최적화용).

    순서 정할 땐 직선거리 추정만 씀. 실 API로 채우면 O(n^2)라 무료 쿼터(대중교통 10/일)로는
    데모 한 번도 못 돌림. 2~5곳 규모면 추정만으로도 순서가 실제랑 거의 같음.
    정확한 거리/시간이랑 지도용 실제 경로는 확정된 최종 경로에만 route-eta가 실 API(n-1회)로 구함.
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

    # 행렬은 항상 추정이지만, UI에 표시할 '추정' 여부는 최종 경로 기준으로 봄
    # (키 있는 모드면 최종 경로는 실측이라 estimated=False).
    estimated = is_estimated_mode(travel_mode)
    return time_matrix, distance_matrix, estimated


# 최적화 모드 3종
# 세 모드 다 출발지(0)는 고정하고 나머지 목적지 순서만 정하는 open-path 문제.
# time_matrix는 build_time_distance_matrix로 구한 n x n 행렬(초).


def path_travel_time(order: List[int], time_matrix: List[List[int]]) -> int:
    # 0(출발지)부터 order 순서대로 돌 때 총 이동시간(초)
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
    """이동시간만 최소화(우선순위 무시). 출발시각 있으면 약속 어기는 순서엔
    큰 페널티 얹어서 약속 지키는 순서가 먼저 뽑히게 함."""
    if len(stop_indices) <= MAX_BRUTE_FORCE_STOPS:
        best_order, best_cost = None, None
        for perm in itertools.permutations(stop_indices):
            cost = path_travel_time(list(perm), time_matrix)
            if all_locations is not None:
                cost += appointment_penalty_sec(list(perm), all_locations, time_matrix, start_min)
            if best_cost is None or cost < best_cost:
                best_cost, best_order = cost, list(perm)
        return best_order

    # 장소 너무 많으면 OR-Tools 근사
    return solve_with_ortools(stop_indices, time_matrix, cost_fn=None)


def solve_priority(stop_indices: List[int], time_matrix: List[List[int]], locations: List[LocationItem]) -> List[int]:
    """
    우선순위 그룹 순서를 무조건 지킴.
    중요한(숫자 작은) 그룹을 반드시 먼저 다 돌고, 같은 그룹 안에서만 이동시간 최소.
    DP(bucket, end_node) -> (cost, path)로 정확히 계산.
    """
    buckets: Dict[int, List[int]] = {}
    for idx in stop_indices:
        p = clamp_priority(locations[idx].priority)
        buckets.setdefault(p, []).append(idx)

    ordered_bucket_keys = sorted(buckets.keys())  # 1(최우선) -> 5

    # dp: {end_node: (cost, path)} 출발지(0)에서 cost 0으로 시작
    dp: Dict[int, Tuple[int, List[int]]] = {0: (0, [])}

    for key in ordered_bucket_keys:
        bucket_nodes = buckets[key]
        new_dp: Dict[int, Tuple[int, List[int]]] = {}

        for prev_end, (prev_cost, prev_path) in dp.items():
            # 그룹 내부 순서 전부 시도(그룹은 보통 작음)
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

    # 마지막 그룹까지 끝나면 비용 최소 경로 선택
    best_end = min(dp, key=lambda k: dp[k][0])
    return dp[best_end][1]


def solve_ai(
    stop_indices: List[int],
    time_matrix: List[List[int]],
    locations: List[LocationItem],
    start_min: Optional[int] = None,
) -> List[int]:
    """
    이동시간 + (중요도 x 방문순서 위치) 점수를 최소화.
    중요한 곳일수록 늦게 가면 페널티 커지게 importance(6-priority)를 곱함.
    (예전엔 순서 무관하게 총합이 같아지던 버그가 있었는데, position 곱하면서 순서가 점수에 먹힘.)

    출발시각 있으면 약속 위반 페널티도 더해서, 약속 지키는 순서가 우선순위/이동시간보다 먼저 뽑힘.
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

    # 장소 많으면 OR-Tools에 "다음 노드 중요도 x 대략 순번" 비용 실어서 근사.
    def cost_fn(from_node, to_node, position):
        return time_matrix[from_node][to_node] + importance_score(locations[to_node].priority) * position * AI_PRIORITY_WEIGHT_SEC

    return solve_with_ortools(stop_indices, time_matrix, cost_fn=cost_fn)


def solve_with_ortools(stop_indices: List[int], time_matrix: List[List[int]], cost_fn=None) -> List[int]:
    """
    장소가 MAX_BRUTE_FORCE_STOPS 넘는 예외적인 경우의 근사.
    cost_fn 없으면 이동시간만, 있으면 그 비용함수 사용.
    (OR-Tools 특성상 position은 정확한 전역 순번이 아니라 근사치임)
    """
    node_list = [0] + stop_indices  # 0(출발지) 다시 넣어서 로컬 인덱스 구성
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
        # position 자리에 to-node 로컬 인덱스 사용(정확한 전역 순번은 아님)
        return cost_fn(node_list[i], node_list[j], j)

    callback_index = routing.RegisterTransitCallback(transit_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(callback_index)

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    # 장소 많을 때(n>=10)만 옴. 이 규모에선 2초나 5초나 품질 비슷해서 응답 빠르게 2초로.
    parameters.time_limit.seconds = 2

    solution = routing.SolveWithParameters(parameters)
    if solution is None:
        return stop_indices  # 최후의 폴백: 원래 순서

    order = []
    index = routing.Start(0)
    index = solution.Value(routing.NextVar(index))  # 출발지 다음부터
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        order.append(node_list[node])
        index = solution.Value(routing.NextVar(index))

    return order


# /api/optimize-route

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
    stop_indices = list(range(1, len(all_locations)))  # 0=출발지, 나머지 목적지

    if req.optimize_mode == "shortest":
        order = solve_shortest(stop_indices, time_matrix, all_locations, start_min)
    elif req.optimize_mode == "priority":
        order = solve_priority(stop_indices, time_matrix, all_locations)
    else:  # "ai"
        order = solve_ai(stop_indices, time_matrix, all_locations, start_min)

    optimized_locations = [all_locations[0]] + [all_locations[i] for i in order]

    # 확정 순서로 시간축 계산해서 약속 위반도 같이 알려줌.
    # (여기 time_matrix는 추정이라 정확한 도착시각은 route-eta가 계산)
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


# 최종 경로 실제 거리/시간

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
            # 최종 경로라 실제 좌표(path)까지 받아서 지도에 그림. get_final_leg가 캐시해줌.
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
                # 실 경로 좌표(자동차=도로, 도보=인도, 대중교통=정류장). 추정 폴백이면 빈 리스트.
                "path": path,
            })

    # 인접 구간 이동시간으로 시간행렬 구성(스케줄/역산에 재사용).
    n = len(locs)
    time_matrix = [[0] * n for _ in range(n)]
    for i in range(n - 1):
        time_matrix[i][i + 1] = max(1, int(round(leg_secs[i])))
    full_order = list(range(n))

    # 유효 출발시각:
    #  - 지정했으면 그 시각
    #  - 미지정인데 약속 있으면 '지킬 수 있는 가장 늦은 출발시각' 역산
    #  - 미지정 + 약속 없음이면 시간축 계산 안 함
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

    # 유효 출발시각 있으면 시간축(도착/출발/종료/위반) 계산해서 붙임.
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
        # 역산으로 정한 경우 '추천 출발시각'이라고 같이 알려줌.
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
        "odsay_configured": bool(ODSAY_API_KEY),
    }
