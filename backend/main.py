import os
import json
import math
import itertools
from typing import List, Optional, Dict, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai

from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp


load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


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
# 값을 늘리면 "우선순위를 얼마나 강하게 반영할지"가 커진다. (기본값은 이동시간과 균형 잡힌 정도)
AI_PRIORITY_WEIGHT_SEC = 300  # 5분


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


class OptimizeRequest(BaseModel):
    start_location: LocationItem
    locations: List[LocationItem]
    travel_mode: str = "car"
    # shortest : 우선순위 완전 무시, 순수 이동시간 최소화
    # priority : 우선순위 그룹 순서를 절대 기준으로 강제, 그룹 내에서만 이동시간 최소화
    # ai       : 이동시간 + (우선순위 x 방문순서) 페널티를 종합한 점수 최소화
    optimize_mode: str = "ai"


class RouteDetailRequest(BaseModel):
    ordered_locations: List[LocationItem]
    travel_mode: str = "car"


# =========================================================
# Common helpers
# =========================================================

def clamp_priority(value: Optional[int]) -> int:
    try:
        value = int(value or 3)
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
# 1. Gemini 장소 추출
# =========================================================

@app.post("/api/parse-tasks")
async def parse_tasks(req: ParseRequest):
    prompt = f"""
아래 일정 문장에서 방문해야 할 장소를 모두 추출해 JSON 배열로 반환해줘.

각 항목은 반드시 다음 필드를 가져야 한다.
- name: 장소명
- task: 그 장소에서 해야 할 일
- priority: 중요도 1~5, 1이 가장 중요함
- lat: 숫자
- lng: 숫자
- address: 알고 있다면 주소, 모르면 빈 문자열

장소명이 애매하면 가장 유력한 장소명을 사용한다.
응답에는 JSON 배열만 포함한다.

입력:
{req.user_text}
"""

    if not GEMINI_API_KEY:
        return {"status": "success", "data": mock_data(), "is_mock": True}

    try:
        model = genai.GenerativeModel(
            GEMINI_MODEL,
            generation_config={"response_mime_type": "application/json"},
        )
        response = model.generate_content(prompt)
        parsed = json.loads(response.text)

        if not isinstance(parsed, list):
            raise ValueError("Gemini 응답이 배열이 아닙니다.")

        normalized = []
        for item in parsed:
            normalized.append(
                {
                    "name": str(item.get("name", "장소")),
                    "task": str(item.get("task", "방문")),
                    "priority": clamp_priority(item.get("priority", 3)),
                    "lat": float(item.get("lat", 0)),
                    "lng": float(item.get("lng", 0)),
                    "address": str(item.get("address", "")),
                }
            )

        if not normalized:
            raise ValueError("장소가 추출되지 않았습니다.")

        return {"status": "success", "data": normalized, "is_mock": False}

    except Exception as e:
        print("Gemini parsing failed:", e)
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

async def get_car_leg(origin: LocationItem, dest: LocationItem, client: httpx.AsyncClient):
    """Kakao Mobility 자동차 길찾기. 반환: duration seconds, distance meters"""
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

    summary = data["routes"][0]["summary"]
    return float(summary["duration"]), float(summary["distance"])


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

    return duration_sec, distance_m


async def get_leg_duration(origin: LocationItem, dest: LocationItem, mode: str, client: httpx.AsyncClient):
    if mode == "car" and KAKAO_REST_API_KEY:
        try:
            return await get_car_leg(origin, dest, client)
        except Exception as e:
            print(f"Kakao 자동차 API 실패: {origin.name} -> {dest.name}: {e}")

    return fallback_leg(origin, dest, mode)


async def build_time_distance_matrix(locations: List[LocationItem], travel_mode: str):
    """모든 장소 쌍의 이동시간/거리 행렬. 최적화 비용의 기준 데이터가 된다."""
    n = len(locations)
    time_matrix = [[0] * n for _ in range(n)]
    distance_matrix = [[0] * n for _ in range(n)]
    estimated = travel_mode != "car" or not KAKAO_REST_API_KEY

    async with httpx.AsyncClient() as client:
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                duration, distance = await get_leg_duration(locations[i], locations[j], travel_mode, client)
                time_matrix[i][j] = max(1, int(round(duration)))
                distance_matrix[i][j] = max(1, int(round(distance)))

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


def solve_shortest(stop_indices: List[int], time_matrix: List[List[int]]) -> List[int]:
    """순수 이동시간 최소화. 우선순위는 완전히 무시한다."""
    if len(stop_indices) <= MAX_BRUTE_FORCE_STOPS:
        best_order, best_cost = None, None
        for perm in itertools.permutations(stop_indices):
            cost = path_travel_time(list(perm), time_matrix)
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


def solve_ai(stop_indices: List[int], time_matrix: List[List[int]], locations: List[LocationItem]) -> List[int]:
    """
    이동시간 + (중요도 x 방문 순서 위치) 페널티를 합산한 점수를 최소화.
    중요한 장소(priority 숫자가 작음)일수록 "늦게 방문할 때" 페널티가 커지도록
    importance_score(= 6 - priority)를 가중치로 곱한다.
    순서 자체가 점수에 실질적으로 반영된다 (기존 버그였던 "순서 무관 총합 동일" 문제 해결).
    """
    if len(stop_indices) <= MAX_BRUTE_FORCE_STOPS:
        best_order, best_score = None, None
        for perm in itertools.permutations(stop_indices):
            travel = path_travel_time(list(perm), time_matrix)
            penalty = sum(
                importance_score(locations[node].priority) * position * AI_PRIORITY_WEIGHT_SEC
                for position, node in enumerate(perm)
            )
            score = travel + penalty
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

    stop_indices = list(range(1, len(all_locations)))  # 0은 출발지, 나머지가 목적지

    if req.optimize_mode == "shortest":
        order = solve_shortest(stop_indices, time_matrix)
    elif req.optimize_mode == "priority":
        order = solve_priority(stop_indices, time_matrix, all_locations)
    else:  # "ai"
        order = solve_ai(stop_indices, time_matrix, all_locations)

    optimized_locations = [all_locations[0]] + [all_locations[i] for i in order]

    return {
        "status": "success",
        "optimized_locations": [loc.model_dump() for loc in optimized_locations],
        "optimize_mode": req.optimize_mode,
        "travel_mode": req.travel_mode,
        "estimated": estimated,
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
    estimated = req.travel_mode != "car" or not KAKAO_REST_API_KEY

    async with httpx.AsyncClient() as client:
        for i in range(len(locs) - 1):
            origin, dest = locs[i], locs[i + 1]
            duration, distance = await get_leg_duration(origin, dest, req.travel_mode, client)
            total_duration += duration
            total_distance += distance
            legs.append({
                "from": origin.name,
                "to": dest.name,
                "duration_min": round(duration / 60, 1),
                "distance_km": round(distance / 1000, 2),
            })

    return {
        "status": "success",
        "total_duration_min": round(total_duration / 60),
        "total_distance_km": round(total_distance / 1000, 1),
        "legs": legs,
        "estimated": estimated,
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "gemini_configured": bool(GEMINI_API_KEY),
        "kakao_rest_configured": bool(KAKAO_REST_API_KEY),
        "gemini_model": GEMINI_MODEL,
    }
