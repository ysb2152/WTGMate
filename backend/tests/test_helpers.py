"""
main.py의 순수 헬퍼 함수(clamp_priority, importance_score, haversine_distance_m)에 대한
단위 테스트. 외부 API(Gemini/Kakao) 호출이 없는 로직만 다루므로 별도 키 설정 없이 실행 가능하다.

실행 방법:
    cd backend
    python -m unittest discover -s tests -v
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import clamp_priority, importance_score, haversine_distance_m, LocationItem


class ClampPriorityTests(unittest.TestCase):
    def test_within_range_is_unchanged(self):
        self.assertEqual(clamp_priority(1), 1)
        self.assertEqual(clamp_priority(3), 3)
        self.assertEqual(clamp_priority(5), 5)

    def test_below_range_is_clamped_to_min(self):
        self.assertEqual(clamp_priority(-10), 1)

    def test_zero_is_clamped_to_min_not_treated_as_falsy_default(self):
        # `value or 3` 식이었다면 0이 falsy로 취급되어 3(기본값)으로 바뀌는 버그가 있었다.
        self.assertEqual(clamp_priority(0), 1)

    def test_above_range_is_clamped_to_max(self):
        self.assertEqual(clamp_priority(6), 5)
        self.assertEqual(clamp_priority(100), 5)

    def test_none_defaults_to_three(self):
        self.assertEqual(clamp_priority(None), 3)

    def test_non_numeric_defaults_to_three(self):
        self.assertEqual(clamp_priority("not-a-number"), 3)


class ImportanceScoreTests(unittest.TestCase):
    def test_priority_one_is_highest_importance(self):
        self.assertEqual(importance_score(1), 5)

    def test_priority_five_is_lowest_importance(self):
        self.assertEqual(importance_score(5), 1)

    def test_is_monotonically_decreasing_with_priority(self):
        scores = [importance_score(p) for p in range(1, 6)]
        self.assertEqual(scores, sorted(scores, reverse=True))


class HaversineDistanceTests(unittest.TestCase):
    def test_same_point_is_zero(self):
        gangnam = LocationItem(name="강남역", task="", lat=37.4979, lng=127.0276)
        self.assertEqual(haversine_distance_m(gangnam, gangnam), 0)

    def test_matches_known_degree_distance_at_equator(self):
        # 적도에서 경도 1도 차이는 지구 반지름 기준으로 약 111.2km에 해당한다 (교과서적 상수).
        a = LocationItem(name="A", task="", lat=0.0, lng=0.0)
        b = LocationItem(name="B", task="", lat=0.0, lng=1.0)

        distance_m = haversine_distance_m(a, b)

        self.assertAlmostEqual(distance_m / 1000, 111.2, delta=0.5)

    def test_is_symmetric(self):
        a = LocationItem(name="A", task="", lat=37.5, lng=127.0)
        b = LocationItem(name="B", task="", lat=37.6, lng=127.1)

        self.assertAlmostEqual(haversine_distance_m(a, b), haversine_distance_m(b, a))


if __name__ == "__main__":
    unittest.main()
