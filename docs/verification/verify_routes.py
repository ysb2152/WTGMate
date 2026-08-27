"""WTGMate 경로 계산 전수 검증 하네스.

UI가 호출하는 백엔드 API(/api/parse-tasks, /api/optimize-route, /api/route-eta)를
직접 구동해 10개 시나리오 x 3 이동수단(car/walk/transit) x 3 최적화모드(ai/shortest/priority)를
전부 계산하고, 정합성 불변식을 검사한다.

출력:
  docs/verification/results.json          - 원시 결과(전 계산값)
  docs/verification/verification-report.md - 사람이 읽는 검증 리포트
"""
import os
import json
import time
import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

# 이 파일(docs/verification/verify_routes.py) 위치에서 레포 루트를 역산한다.
# (경로 하드코딩 대신 파일 기준으로 잡아, 레포가 옮겨지거나 이름이 바뀌어도 깨지지 않게 한다.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = _REPO_ROOT / "backend"
OUT_DIR = _REPO_ROOT / "docs" / "verification"
BASE = "http://127.0.0.1:8000"

load_dotenv(BACKEND / ".env")
KAKAO = os.getenv("KAKAO_REST_API_KEY")

TRAVEL_MODES = ["car", "walk", "transit"]
OPT_MODES = ["ai", "shortest", "priority"]

NOW_HHMM = datetime.datetime.now().strftime("%H:%M")

# start_time 표기: "HH:MM"=명시, None=시간제약 없음, "BLANK"=UI처럼 현재 PC시각 사용
SCENARIOS = [
    {"id": 1,  "start": "서울역",     "schedule": "오후 3시 강남역에서 미팅, 시간 되면 교보문고 들러 책 구경", "start_time": "14:00"},
    {"id": 2,  "start": "홍대입구역", "schedule": "여의도 국회의사당 들르고 남산서울타워 구경",                 "start_time": None},
    {"id": 3,  "start": "강남역",     "schedule": "서울시청 민원 보고, 광화문에서 점심, 동대문 쇼핑",           "start_time": "BLANK"},
    {"id": 4,  "start": "부산역",     "schedule": "해운대 해수욕장 산책하고 저녁 6시 광안리에서 저녁",           "start_time": "16:00"},
    {"id": 5,  "start": "잠실역",     "schedule": "롯데월드타워 전망대 구경하고 석촌호수 산책",                 "start_time": None},
    {"id": 6,  "start": "수원역",     "schedule": "오전 10시 수원화성 관광하고 행궁동 카페에서 커피",           "start_time": "09:30"},
    {"id": 7,  "start": "인천국제공항","schedule": "송도 센트럴파크 구경하고 오후 1시 차이나타운에서 점심",       "start_time": "11:30"},
    {"id": 8,  "start": "대전역",     "schedule": "성심당 들러 빵 사고 한밭수목원 산책",                       "start_time": "BLANK"},
    {"id": 9,  "start": "서울역",     "schedule": "명동 쇼핑하고 남대문시장 구경, 서울로7017 산책, 덕수궁 관람", "start_time": "13:00"},
    {"id": 10, "start": "청량리역",   "schedule": "경동시장에서 장보고 서울약령시 구경",                        "start_time": None},
]


def kakao_geocode(name):
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    r = httpx.get(url, headers={"Authorization": f"KakaoAK {KAKAO}"}, params={"query": name, "size": 1}, timeout=10)
    r.raise_for_status()
    docs = r.json().get("documents") or []
    if not docs:
        raise RuntimeError(f"geocode 실패: {name}")
    d = docs[0]
    return {"name": name, "task": "출발지", "priority": 0,
            "lat": float(d["y"]), "lng": float(d["x"]),
            "address": d.get("road_address_name") or d.get("address_name") or "",
            "appointment_time": None, "duration_min": 0}


def parse_tasks(text):
    r = httpx.post(f"{BASE}/api/parse-tasks", json={"user_text": text}, timeout=180)
    r.raise_for_status()
    j = r.json()
    return j.get("data", []), j.get("is_mock", False)


def optimize(start_loc, locations, travel_mode, optimize_mode, start_time):
    payload = {"start_location": start_loc, "locations": locations,
               "travel_mode": travel_mode, "optimize_mode": optimize_mode,
               "start_time": start_time}
    r = httpx.post(f"{BASE}/api/optimize-route", json=payload, timeout=180)
    r.raise_for_status()
    return r.json()


def route_eta(ordered, travel_mode, start_time):
    payload = {"ordered_locations": ordered, "travel_mode": travel_mode, "start_time": start_time}
    r = httpx.post(f"{BASE}/api/route-eta", json=payload, timeout=180)
    r.raise_for_status()
    return r.json()


def effective_start(raw):
    if raw == "BLANK":
        return NOW_HHMM
    return raw  # "HH:MM" 또는 None


def run():
    results = []
    for sc in SCENARIOS:
        eff = effective_start(sc["start_time"])
        rec = {"id": sc["id"], "start": sc["start"], "schedule": sc["schedule"],
               "start_time_raw": sc["start_time"], "start_time_effective": eff,
               "places": None, "is_mock": None, "runs": {}, "checks": [], "fixed_order_eta": {}}
        try:
            start_loc = kakao_geocode(sc["start"])
            places, is_mock = parse_tasks(sc["schedule"])
            rec["is_mock"] = is_mock
            rec["places"] = [{"name": p["name"], "priority": p["priority"],
                              "appointment_time": p.get("appointment_time")} for p in places]

            for tm in TRAVEL_MODES:
                for om in OPT_MODES:
                    opt = optimize(start_loc, places, tm, om, eff)
                    ordered = opt["optimized_locations"]
                    eta = route_eta(ordered, tm, eff)
                    rec["runs"][f"{tm}/{om}"] = {
                        "order": [l["name"] for l in ordered],
                        "order_priorities": [l.get("priority") for l in ordered[1:]],
                        "total_duration_min": eta["total_duration_min"],
                        "total_distance_km": eta["total_distance_km"],
                        "estimated": eta.get("estimated"),
                        "appointment_violations": eta.get("appointment_violations", []),
                        "finish_time": eta.get("finish_time"),
                        "start_time_used": eta.get("start_time"),
                        "legs": [{"from": lg["from"], "to": lg["to"],
                                  "min": lg["duration_min"], "km": lg["distance_km"]} for lg in eta["legs"]],
                    }

            # Check D용: 파싱된 원래 순서(고정)로 이동수단만 바꿔 eta 비교
            fixed_ordered = [start_loc] + places
            for tm in TRAVEL_MODES:
                e = route_eta(fixed_ordered, tm, eff)
                rec["fixed_order_eta"][tm] = {"total_duration_min": e["total_duration_min"],
                                              "total_distance_km": e["total_distance_km"]}

            rec["checks"] = run_checks(rec)
        except Exception as ex:
            rec["error"] = f"{type(ex).__name__}: {ex}"
        results.append(rec)
        print(f"[시나리오 {sc['id']}] 완료" + (f" (ERROR: {rec.get('error')})" if rec.get("error") else ""))
    return results


def run_checks(rec):
    checks = []
    runs = rec["runs"]
    eff = rec["start_time_effective"]
    has_appt = any(p.get("appointment_time") for p in rec["places"])
    appt_active = eff is not None and has_appt

    # Check C: 모든 leg 양수 & 총합=leg합 정합
    for key, r in runs.items():
        legs = r["legs"]
        bad_leg = any((lg["min"] <= 0 or lg["km"] <= 0) for lg in legs)
        sum_min = sum(lg["min"] for lg in legs)
        # total_duration_min은 반올림(정수), 소수합과 오차 허용
        consistent = abs(sum_min - r["total_duration_min"]) <= max(1.0, 0.05 * max(1, r["total_duration_min"]))
        checks.append({"name": f"C(legs>0 & 합정합) [{key}]",
                       "pass": (not bad_leg) and consistent,
                       "detail": f"leg합={round(sum_min,1)}분, total={r['total_duration_min']}분, 음수leg={bad_leg}"})

    # Check A: 각 이동수단에서 shortest 총 이동시간 <= ai, priority (약속제약 없을 때만 강제)
    for tm in TRAVEL_MODES:
        s = runs[f"{tm}/shortest"]["total_duration_min"]
        a = runs[f"{tm}/ai"]["total_duration_min"]
        p = runs[f"{tm}/priority"]["total_duration_min"]
        ok = s <= a + 0.5 and s <= p + 0.5
        checks.append({"name": f"A(shortest<=ai,priority) [{tm}]",
                       "pass": (ok or appt_active),
                       "detail": f"shortest={s}, ai={a}, priority={p}" + (" (약속제약 활성-참고용)" if appt_active and not ok else "")})

    # Check B: priority 모드에서 방문 우선순위가 비감소(그룹 순서 준수)
    for tm in TRAVEL_MODES:
        pr = runs[f"{tm}/priority"]["order_priorities"]
        nondec = all(pr[i] <= pr[i+1] for i in range(len(pr)-1))
        checks.append({"name": f"B(priority 순서 비감소) [{tm}]",
                       "pass": nondec, "detail": f"우선순위열={pr}"})

    # Check D: 고정 순서에서 도보 >= 자동차 이동시간(도보가 더 느림)
    fe = rec["fixed_order_eta"]
    if all(tm in fe for tm in ("car", "walk")):
        d_ok = fe["walk"]["total_duration_min"] >= fe["car"]["total_duration_min"]
        checks.append({"name": "D(도보>=자동차 시간, 고정순서)",
                       "pass": d_ok,
                       "detail": f"car={fe['car']['total_duration_min']}분, walk={fe['walk']['total_duration_min']}분, transit={fe['transit']['total_duration_min']}분"})

    # Check E: 약속제약 활성 시, ai/priority 모드는 위반 0 이어야 함(약속 지키는 순서 선택)
    if appt_active:
        for om in ("ai", "priority"):
            v = runs[f"car/{om}"]["appointment_violations"]
            checks.append({"name": f"E(약속위반 0) [car/{om}]",
                           "pass": len(v) == 0, "detail": f"위반={v}"})

    return checks


def write_report(results):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    total_checks = sum(len(r.get("checks", [])) for r in results)
    passed = sum(1 for r in results for c in r.get("checks", []) if c["pass"])
    errors = [r for r in results if r.get("error")]

    L = []
    L.append("# WTGMate 경로 계산 검증 리포트")
    L.append("")
    L.append(f"- 생성 시각: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"- 백엔드: `{BASE}` (모델 `wtgmate-parser`)")
    L.append(f"- 시나리오: {len(results)}개 x 이동수단 3(car/walk/transit) x 최적화 3(ai/shortest/priority) = 계산 {len(results)*9}건")
    L.append(f"- 현재 PC 시각(BLANK 출발시각 대체값): `{NOW_HHMM}`")
    L.append(f"- **정합성 검사 통과: {passed}/{total_checks}**" + (f" · 오류 시나리오 {len(errors)}개" if errors else ""))
    L.append("")
    L.append("## 검사 항목 정의")
    L.append("- **A**: 각 이동수단에서 `shortest` 모드의 총 이동시간이 `ai`·`priority`보다 작거나 같아야 함(순수 최단이 최소). 단, 출발시각+약속이 동시에 있으면 약속 준수를 위해 더 길 수 있어 참고용 처리.")
    L.append("- **B**: `priority` 모드의 방문 순서에서 우선순위 값이 비감소(1→…→5, 중요 그룹 먼저).")
    L.append("- **C**: 모든 구간(leg) 이동시간·거리 > 0, 그리고 leg 합 ≈ 총 이동시간(반올림 오차 허용).")
    L.append("- **D**: 동일(고정) 방문 순서에서 도보 총 이동시간 ≥ 자동차(도보가 느림).")
    L.append("- **E**: 출발시각+약속이 활성일 때 `ai`·`priority`(car) 모드의 약속 위반이 0.")
    L.append("")

    for r in results:
        L.append(f"## 시나리오 {r['id']} — 출발: {r['start']}")
        L.append("")
        L.append(f"- 일정: \"{r['schedule']}\"")
        st = r["start_time_raw"]
        st_desc = "없음(시간제약 X)" if st is None else (f"빈칸→현재시각 `{r['start_time_effective']}`" if st == "BLANK" else f"`{st}`")
        L.append(f"- 출발 시각: {st_desc}")
        if r.get("error"):
            L.append(f"- ⚠️ **오류**: {r['error']}")
            L.append("")
            continue
        L.append(f"- 추출 장소({'MOCK' if r['is_mock'] else '실추출'}): " +
                 ", ".join(f"{p['name']}(P{p['priority']}" + (f", 약속 {p['appointment_time']}" if p.get('appointment_time') else "") + ")" for p in r["places"]))
        L.append("")
        L.append("| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |")
        L.append("|---|---|---|---|---|---|---|")
        for tm in TRAVEL_MODES:
            for om in OPT_MODES:
                run = r["runs"][f"{tm}/{om}"]
                order = " → ".join(run["order"])
                viol = ",".join(run["appointment_violations"]) or "-"
                L.append(f"| {tm} | {om} | {run['total_duration_min']} | {run['total_distance_km']} | {order} | {viol} | {run['finish_time'] or '-'} |")
        L.append("")
        L.append("검사 결과:")
        for c in r["checks"]:
            L.append(f"- {'✅' if c['pass'] else '❌'} {c['name']} — {c['detail']}")
        L.append("")

    (OUT_DIR / "verification-report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"\n총 검사 {passed}/{total_checks} 통과, 오류 {len(errors)}개")
    print(f"리포트: {OUT_DIR/'verification-report.md'}")


if __name__ == "__main__":
    t0 = time.time()
    res = run()
    write_report(res)
    print(f"소요 {time.time()-t0:.0f}s")
