"""최종 경로 leg 캐시(get_final_leg)의 동작 검증.

실 API를 흉내 내는 가짜 함수를 주입해, 캐시 히트/미스·LRU 상한·TTL 만료·
폴백(path 없음) 미캐시를 외부 호출 없이 검증한다.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from main import LocationItem


def loc(i):
    return LocationItem(name=f"p{i}", task="t", lat=37.5 + i * 0.001, lng=127.0 + i * 0.001)


class DummyClient:
    async def get(self, *a, **k):
        raise AssertionError("get_final_leg 경로에서 실제 client.get이 호출되면 안 된다")


class GetFinalLegCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main._FINAL_LEG_CACHE.clear()
        self._orig = main.get_leg_duration
        self._orig_max = main._FINAL_LEG_CACHE_MAX
        self._orig_now = main._now
        self.calls = 0

    def tearDown(self):
        main.get_leg_duration = self._orig
        main._FINAL_LEG_CACHE_MAX = self._orig_max
        main._now = self._orig_now
        main._FINAL_LEG_CACHE.clear()

    def _patch_real(self, path):
        async def fake(origin, dest, mode, client, include_path=False):
            self.calls += 1
            return 600.0, 800.0, list(path)
        main.get_leg_duration = fake

    async def test_second_call_hits_cache(self):
        self._patch_real([[37.5, 127.0], [37.6, 127.1]])
        c = DummyClient()
        a = await main.get_final_leg(loc(0), loc(1), "walk", c)
        b = await main.get_final_leg(loc(0), loc(1), "walk", c)
        self.assertEqual(self.calls, 1)  # 두 번째는 캐시 히트
        self.assertEqual(a, b)

    async def test_fallback_without_path_is_not_cached(self):
        self._patch_real([])  # 폴백 흉내: path 비어 있음
        c = DummyClient()
        await main.get_final_leg(loc(0), loc(1), "transit", c)
        await main.get_final_leg(loc(0), loc(1), "transit", c)
        self.assertEqual(self.calls, 2)  # 캐시 안 됨 → 매번 호출

    async def test_walk_cache_does_not_expire(self):
        self._patch_real([[37.5, 127.0], [37.6, 127.1]])
        base = [1000.0]
        main._now = lambda: base[0]
        c = DummyClient()
        await main.get_final_leg(loc(0), loc(1), "walk", c)
        base[0] = 1000.0 + 10 ** 6  # 아주 오래 지나도
        await main.get_final_leg(loc(0), loc(1), "walk", c)
        self.assertEqual(self.calls, 1)  # 도보는 TTL 없음(무기한)

    async def test_car_cache_expires_after_ttl(self):
        self._patch_real([[37.5, 127.0], [37.6, 127.1]])
        base = [1000.0]
        main._now = lambda: base[0]
        c = DummyClient()
        await main.get_final_leg(loc(0), loc(1), "car", c)   # 저장(만료 1000+600)
        base[0] = 1000.0 + 300                                # 만료 전
        await main.get_final_leg(loc(0), loc(1), "car", c)
        self.assertEqual(self.calls, 1)
        base[0] = 1000.0 + 700                                # 만료 후
        await main.get_final_leg(loc(0), loc(1), "car", c)
        self.assertEqual(self.calls, 2)

    async def test_lru_evicts_oldest_over_capacity(self):
        self._patch_real([[37.5, 127.0], [37.6, 127.1]])
        main._FINAL_LEG_CACHE_MAX = 3
        main._now = lambda: 0.0
        c = DummyClient()
        for i in range(5):
            await main.get_final_leg(loc(i), loc(i + 100), "walk", c)
        self.assertLessEqual(len(main._FINAL_LEG_CACHE), 3)


if __name__ == "__main__":
    unittest.main()
