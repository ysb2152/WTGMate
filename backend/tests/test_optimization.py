"""경로 최적화 solver(solve_shortest / solve_priority / solve_ai)와 스케줄 계산
(build_schedule / recommended_departure)에 대한 단위 테스트.

독립적인 완전탐색(브루트포스) 결과와 대조해 solver가 실제로 최적해를 내는지 검증한다.
외부 API 호출 없이 순수 계산만 다루므로 키 없이 실행 가능하다.

실행:
    cd backend
    python -m unittest discover -s tests -v
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from main import (
    LocationItem,
    path_travel_time,
    importance_score,
    solve_shortest,
    solve_priority,
    solve_ai,
    build_schedule,
    recommended_departure,
    AI_PRIORITY_WEIGHT_SEC,
)


def loc(name, priority=3, appt=None, dwell=0):
    # 좌표는 solver 계산에 쓰이지 않으므로(행렬을 직접 주입) 더미값.
    return LocationItem(name=name, task="t", priority=priority, lat=37.5, lng=127.0,
                        appointment_time=appt, duration_min=dwell)


# 0=출발지 포함 4개 지점의 비대칭 이동시간 행렬(초). 대칭이 아니어서 순서가 결과에 영향을 준다.
TIME_MATRIX = [
    [0, 600, 900, 300],
    [600, 0, 400, 800],
    [900, 400, 0, 500],
    [300, 800, 500, 0],
]


class SolveShortestTests(unittest.TestCase):
    def test_returns_true_minimum_order(self):
        stops = [1, 2, 3]
        order = solve_shortest(stops, TIME_MATRIX, None, None)
        best = min(path_travel_time(list(p), TIME_MATRIX) for p in itertools.permutations(stops))
        self.assertEqual(path_travel_time(order, TIME_MATRIX), best)

    def test_visits_all_stops_once(self):
        stops = [1, 2, 3]
        order = solve_shortest(stops, TIME_MATRIX, None, None)
        self.assertEqual(sorted(order), stops)


class SolvePriorityTests(unittest.TestCase):
    def test_priority_is_non_decreasing(self):
        # priority 1(중요) 그룹이 5(여유)보다 반드시 먼저 방문돼야 한다.
        locs = [loc("start", 0), loc("A", 5), loc("B", 1), loc("C", 1), loc("D", 3)]
        order = solve_priority([1, 2, 3, 4], TIME_MATRIX_5, locs)
        prios = [locs[i].priority for i in order]
        self.assertEqual(prios, sorted(prios))

    def test_optimal_within_group_ordering(self):
        # 같은 우선순위끼리는 이동시간이 최소인 순서여야 한다(그룹 제약을 지키는 순열 중 최소).
        locs = [loc("start", 0), loc("A", 1), loc("B", 1), loc("C", 2)]
        order = solve_priority([1, 2, 3], TIME_MATRIX, locs)
        cost = path_travel_time(order, TIME_MATRIX)
        # 우선순위 비감소를 만족하는 순열들 중 최소 비용과 같아야 한다.
        feasible = [
            p for p in itertools.permutations([1, 2, 3])
            if all(locs[p[i]].priority <= locs[p[i + 1]].priority for i in range(len(p) - 1))
        ]
        best = min(path_travel_time(list(p), TIME_MATRIX) for p in feasible)
        self.assertEqual(cost, best)


class SolveAiTests(unittest.TestCase):
    def _ai_score(self, order, locs):
        travel = path_travel_time(order, TIME_MATRIX)
        penalty = sum(importance_score(locs[n].priority) * pos * AI_PRIORITY_WEIGHT_SEC
                      for pos, n in enumerate(order))
        return travel + penalty

    def test_minimizes_travel_plus_priority_penalty(self):
        locs = [loc("start", 0), loc("A", 1), loc("B", 5), loc("C", 3)]
        order = solve_ai([1, 2, 3], TIME_MATRIX, locs, None)
        best = min(self._ai_score(list(p), locs) for p in itertools.permutations([1, 2, 3]))
        self.assertEqual(self._ai_score(order, locs), best)

    def test_important_place_goes_first_when_travel_is_neutral(self):
        # 이동시간이 모든 쌍에서 동일하면, ai는 우선순위가 높은(숫자가 작은) 곳을 먼저 방문한다.
        locs = [loc("start", 0), loc("important", 1), loc("trivial", 5)]
        m = [[0, 300, 300], [300, 0, 300], [300, 300, 0]]  # 모든 이동 300초 동일
        ai = solve_ai([1, 2], m, locs, None)
        self.assertEqual(ai[0], 1)  # 이동시간이 같으니 중요한 곳을 먼저


# solve_priority 테스트용 5지점 행렬.
TIME_MATRIX_5 = [
    [0, 600, 900, 300, 700],
    [600, 0, 400, 800, 200],
    [900, 400, 0, 500, 600],
    [300, 800, 500, 0, 450],
    [700, 200, 600, 450, 0],
]


class BuildScheduleTests(unittest.TestCase):
    def test_arrival_accumulates_travel_time(self):
        locs = [loc("start", 0), loc("A", 3)]
        # start(0)->A(1) 600초 = 10분. 출발 09:00(540분) → 도착 550분.
        m = [[0, 600], [600, 0]]
        schedule, viol, _ = build_schedule([0, 1], locs, m, 540)
        self.assertEqual(schedule[1]["arrival_min"], 550)
        self.assertEqual(viol, [])

    def test_detects_appointment_violation(self):
        # A 약속 09:05인데 09:10 도착 → 위반.
        locs = [loc("start", 0), loc("A", 3, appt="09:05")]
        m = [[0, 600], [600, 0]]  # 10분
        _, viol, _ = build_schedule([0, 1], locs, m, 540)  # 09:00 출발
        self.assertIn(1, viol)

    def test_waits_until_appointment_when_early(self):
        # 약속 10:00인데 09:10 도착 → 10:00까지 대기 후 체류.
        locs = [loc("start", 0), loc("A", 3, appt="10:00", dwell=30)]
        m = [[0, 600], [600, 0]]
        schedule, viol, _ = build_schedule([0, 1], locs, m, 540)
        self.assertEqual(viol, [])
        self.assertEqual(schedule[1]["depart_min"], 600 + 30)  # 10:00 + 체류30분


class RecommendedDepartureTests(unittest.TestCase):
    def test_none_when_no_appointment(self):
        locs = [loc("start", 0), loc("A", 3)]
        m = [[0, 600], [600, 0]]
        dep, feasible = recommended_departure([0, 1], locs, m)
        self.assertIsNone(dep)

    def test_latest_departure_still_makes_appointment(self):
        # A 약속 10:00, 이동 10분 → 가장 늦어도 09:50엔 나가야 한다.
        locs = [loc("start", 0), loc("A", 3, appt="10:00")]
        m = [[0, 600], [600, 0]]
        dep, feasible = recommended_departure([0, 1], locs, m)
        self.assertEqual(dep, 590)  # 09:50
        self.assertTrue(feasible)


if __name__ == "__main__":
    unittest.main()
