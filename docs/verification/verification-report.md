# WTGMate 경로 계산 검증 리포트

- 생성 시각: 2026-08-26 04:25:11
- 백엔드: `http://127.0.0.1:8000` (모델 `wtgmate-parser`)
- 시나리오: 10개 x 이동수단 3(car/walk/transit) x 최적화 3(ai/shortest/priority) = 계산 90건
- 현재 PC 시각(BLANK 출발시각 대체값): `04:24`
- **백엔드 계산엔진 정합성 검사: 168/168 통과 ✅**
- **UI 대조 결과: 자동차 일치 ✅ / 이동수단 전환 시 순서 재최적화 누락 버그 발견 ⚠️ (부록 참고)**

> 요약: 백엔드(optimize-route/route-eta) 계산은 10개 시나리오 × 9조합 전부 정확·정합.
> 다만 프론트에서 **이동수단을 바꾸면 방문 순서를 다시 최적화하지 않고** 이전 이동수단의 순서에
> ETA만 다시 계산하는 버그가 있어, 최적 순서가 이동수단별로 다른 경우 UI가 준최적 경로를 보여준다.

## 검사 항목 정의
- **A**: 각 이동수단에서 `shortest` 모드의 총 이동시간이 `ai`·`priority`보다 작거나 같아야 함(순수 최단이 최소). 단, 출발시각+약속이 동시에 있으면 약속 준수를 위해 더 길 수 있어 참고용 처리.
- **B**: `priority` 모드의 방문 순서에서 우선순위 값이 비감소(1→…→5, 중요 그룹 먼저).
- **C**: 모든 구간(leg) 이동시간·거리 > 0, 그리고 leg 합 ≈ 총 이동시간(반올림 오차 허용).
- **D**: 동일(고정) 방문 순서에서 도보 총 이동시간 ≥ 자동차(도보가 느림).
- **E**: 출발시각+약속이 활성일 때 `ai`·`priority`(car) 모드의 약속 위반이 0.

## 시나리오 1 — 출발: 서울역

- 일정: "오후 3시 강남역에서 미팅, 시간 되면 교보문고 들러 책 구경"
- 출발 시각: `14:00`
- 추출 장소(실추출): 강남역(P1, 약속 15:00), 교보문고 강남점(P4)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 25 | 12.5 | 서울역 → 강남역 → 교보문고 강남점 | - | 15:06 |
| car | shortest | 21 | 12.0 | 서울역 → 교보문고 강남점 → 강남역 | - | 15:00 |
| car | priority | 25 | 12.5 | 서울역 → 강남역 → 교보문고 강남점 | - | 15:06 |
| walk | ai | 128 | 9.6 | 서울역 → 교보문고 강남점 → 강남역 | 강남역 | 16:08 |
| walk | shortest | 128 | 9.6 | 서울역 → 교보문고 강남점 → 강남역 | 강남역 | 16:08 |
| walk | priority | 140 | 10.5 | 서울역 → 강남역 → 교보문고 강남점 | 강남역 | 16:20 |
| transit | ai | 51 | 12.2 | 서울역 → 강남역 → 교보문고 강남점 | - | 15:08 |
| transit | shortest | 47 | 11.2 | 서울역 → 교보문고 강남점 → 강남역 | - | 15:00 |
| transit | priority | 51 | 12.2 | 서울역 → 강남역 → 교보문고 강남점 | - | 15:08 |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=25.1분, total=25분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=20.8분, total=21분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=25.1분, total=25분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=128.5분, total=128분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=128.5분, total=128분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=139.7분, total=140분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=50.7분, total=51분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=47.5분, total=47분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=50.7분, total=51분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=21, ai=25, priority=25
- ✅ A(shortest<=ai,priority) [walk] — shortest=128, ai=128, priority=140
- ✅ A(shortest<=ai,priority) [transit] — shortest=47, ai=51, priority=51
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[1, 4]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[1, 4]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[1, 4]
- ✅ D(도보>=자동차 시간, 고정순서) — car=25분, walk=140분, transit=51분
- ✅ E(약속위반 0) [car/ai] — 위반=[]
- ✅ E(약속위반 0) [car/priority] — 위반=[]

## 시나리오 2 — 출발: 홍대입구역

- 일정: "여의도 국회의사당 들르고 남산서울타워 구경"
- 출발 시각: 없음(시간제약 X)
- 추출 장소(실추출): 국회(P2), 남산서울타워(P3)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 33 | 18.6 | 홍대입구역 → 국회 → 남산서울타워 | - | - |
| car | shortest | 33 | 18.6 | 홍대입구역 → 국회 → 남산서울타워 | - | - |
| car | priority | 33 | 18.6 | 홍대입구역 → 국회 → 남산서울타워 | - | - |
| walk | ai | 156 | 11.7 | 홍대입구역 → 국회 → 남산서울타워 | - | - |
| walk | shortest | 156 | 11.7 | 홍대입구역 → 국회 → 남산서울타워 | - | - |
| walk | priority | 156 | 11.7 | 홍대입구역 → 국회 → 남산서울타워 | - | - |
| transit | ai | 56 | 13.7 | 홍대입구역 → 국회 → 남산서울타워 | - | - |
| transit | shortest | 56 | 13.7 | 홍대입구역 → 국회 → 남산서울타워 | - | - |
| transit | priority | 56 | 13.7 | 홍대입구역 → 국회 → 남산서울타워 | - | - |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=33.3분, total=33분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=33.3분, total=33분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=33.3분, total=33분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=156.4분, total=156분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=156.4분, total=156분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=156.4분, total=156분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=55.6분, total=56분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=55.6분, total=56분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=55.6분, total=56분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=33, ai=33, priority=33
- ✅ A(shortest<=ai,priority) [walk] — shortest=156, ai=156, priority=156
- ✅ A(shortest<=ai,priority) [transit] — shortest=56, ai=56, priority=56
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[2, 3]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[2, 3]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[2, 3]
- ✅ D(도보>=자동차 시간, 고정순서) — car=33분, walk=156분, transit=56분

## 시나리오 3 — 출발: 강남역

- 일정: "서울시청 민원 보고, 광화문에서 점심, 동대문 쇼핑"
- 출발 시각: 빈칸→현재시각 `04:24`
- 추출 장소(실추출): 서울시청(P1), 광화문(P2), 동대문구청(P3)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 40 | 19.0 | 강남역 → 서울시청 → 광화문 → 동대문구청 | - | 05:04 |
| car | shortest | 39 | 17.7 | 강남역 → 동대문구청 → 서울시청 → 광화문 | - | 05:03 |
| car | priority | 40 | 19.0 | 강남역 → 서울시청 → 광화문 → 동대문구청 | - | 05:04 |
| walk | ai | 240 | 18.0 | 강남역 → 동대문구청 → 서울시청 → 광화문 | - | 08:24 |
| walk | shortest | 240 | 18.0 | 강남역 → 동대문구청 → 서울시청 → 광화문 | - | 08:24 |
| walk | priority | 246 | 18.4 | 강남역 → 서울시청 → 광화문 → 동대문구청 | - | 08:30 |
| transit | ai | 87 | 21.5 | 강남역 → 서울시청 → 광화문 → 동대문구청 | - | 05:51 |
| transit | shortest | 85 | 21.0 | 강남역 → 동대문구청 → 서울시청 → 광화문 | - | 05:49 |
| transit | priority | 87 | 21.5 | 강남역 → 서울시청 → 광화문 → 동대문구청 | - | 05:51 |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=40.3분, total=40분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=38.5분, total=39분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=40.3분, total=40분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=240.5분, total=240분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=240.5분, total=240분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=245.9분, total=246분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=86.7분, total=87분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=85.1분, total=85분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=86.7분, total=87분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=39, ai=40, priority=40
- ✅ A(shortest<=ai,priority) [walk] — shortest=240, ai=240, priority=246
- ✅ A(shortest<=ai,priority) [transit] — shortest=85, ai=87, priority=87
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[1, 2, 3]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[1, 2, 3]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[1, 2, 3]
- ✅ D(도보>=자동차 시간, 고정순서) — car=40분, walk=246분, transit=87분

## 시나리오 4 — 출발: 부산역

- 일정: "해운대 해수욕장 산책하고 저녁 6시 광안리에서 저녁"
- 출발 시각: `16:00`
- 추출 장소(실추출): 해운대해수욕장(P3), 광안리해수욕장(P2, 약속 18:00)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 33 | 14.4 | 부산역 → 광안리해수욕장 → 해운대해수욕장 | - | 18:11 |
| car | shortest | 33 | 14.4 | 부산역 → 광안리해수욕장 → 해운대해수욕장 | - | 18:11 |
| car | priority | 33 | 14.4 | 부산역 → 광안리해수욕장 → 해운대해수욕장 | - | 18:11 |
| walk | ai | 192 | 14.4 | 부산역 → 광안리해수욕장 → 해운대해수욕장 | 광안리해수욕장 | 19:12 |
| walk | shortest | 192 | 14.4 | 부산역 → 광안리해수욕장 → 해운대해수욕장 | 광안리해수욕장 | 19:12 |
| walk | priority | 192 | 14.4 | 부산역 → 광안리해수욕장 → 해운대해수욕장 | 광안리해수욕장 | 19:12 |
| transit | ai | 66 | 16.8 | 부산역 → 광안리해수욕장 → 해운대해수욕장 | - | 18:23 |
| transit | shortest | 66 | 16.8 | 부산역 → 광안리해수욕장 → 해운대해수욕장 | - | 18:23 |
| transit | priority | 66 | 16.8 | 부산역 → 광안리해수욕장 → 해운대해수욕장 | - | 18:23 |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=33.0분, total=33분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=33.0분, total=33분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=33.0분, total=33분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=191.6분, total=192분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=191.6분, total=192분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=191.6분, total=192분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=65.9분, total=66분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=65.9분, total=66분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=65.9분, total=66분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=33, ai=33, priority=33
- ✅ A(shortest<=ai,priority) [walk] — shortest=192, ai=192, priority=192
- ✅ A(shortest<=ai,priority) [transit] — shortest=66, ai=66, priority=66
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[2, 3]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[2, 3]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[2, 3]
- ✅ D(도보>=자동차 시간, 고정순서) — car=40분, walk=249분, transit=83분
- ✅ E(약속위반 0) [car/ai] — 위반=[]
- ✅ E(약속위반 0) [car/priority] — 위반=[]

## 시나리오 5 — 출발: 잠실역

- 일정: "롯데월드타워 전망대 구경하고 석촌호수 산책"
- 출발 시각: 없음(시간제약 X)
- 추출 장소(실추출): 롯데월드타워(P2), 석촌호수(P3)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 10 | 3.4 | 잠실역 → 석촌호수 → 롯데월드타워 | - | - |
| car | shortest | 10 | 3.4 | 잠실역 → 석촌호수 → 롯데월드타워 | - | - |
| car | priority | 12 | 3.4 | 잠실역 → 롯데월드타워 → 석촌호수 | - | - |
| walk | ai | 14 | 1.0 | 잠실역 → 롯데월드타워 → 석촌호수 | - | - |
| walk | shortest | 14 | 1.0 | 잠실역 → 롯데월드타워 → 석촌호수 | - | - |
| walk | priority | 14 | 1.0 | 잠실역 → 롯데월드타워 → 석촌호수 | - | - |
| transit | ai | 14 | 1.2 | 잠실역 → 롯데월드타워 → 석촌호수 | - | - |
| transit | shortest | 14 | 1.2 | 잠실역 → 롯데월드타워 → 석촌호수 | - | - |
| transit | priority | 14 | 1.2 | 잠실역 → 롯데월드타워 → 석촌호수 | - | - |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=9.6분, total=10분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=9.6분, total=10분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=12.3분, total=12분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=13.5분, total=14분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=13.5분, total=14분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=13.5분, total=14분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=13.9분, total=14분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=13.9분, total=14분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=13.9분, total=14분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=10, ai=10, priority=12
- ✅ A(shortest<=ai,priority) [walk] — shortest=14, ai=14, priority=14
- ✅ A(shortest<=ai,priority) [transit] — shortest=14, ai=14, priority=14
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[2, 3]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[2, 3]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[2, 3]
- ✅ D(도보>=자동차 시간, 고정순서) — car=12분, walk=14분, transit=14분

## 시나리오 6 — 출발: 수원역

- 일정: "오전 10시 수원화성 관광하고 행궁동 카페에서 커피"
- 출발 시각: `09:30`
- 추출 장소(실추출): 수원화성(P1, 약속 10:00), 행궁동 카페(P2)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 14 | 4.8 | 수원역 → 행궁동 카페 → 수원화성 | - | 10:00 |
| car | shortest | 14 | 4.8 | 수원역 → 행궁동 카페 → 수원화성 | - | 10:00 |
| car | priority | 17 | 6.4 | 수원역 → 수원화성 → 행궁동 카페 | - | 10:06 |
| walk | ai | 48 | 3.6 | 수원역 → 행궁동 카페 → 수원화성 | 수원화성 | 10:18 |
| walk | shortest | 48 | 3.6 | 수원역 → 행궁동 카페 → 수원화성 | 수원화성 | 10:18 |
| walk | priority | 50 | 3.8 | 수원역 → 수원화성 → 행궁동 카페 | 수원화성 | 10:20 |
| transit | ai | 25 | 4.4 | 수원역 → 수원화성 → 행궁동 카페 | - | 10:08 |
| transit | shortest | 24 | 4.2 | 수원역 → 행궁동 카페 → 수원화성 | - | 10:00 |
| transit | priority | 25 | 4.4 | 수원역 → 수원화성 → 행궁동 카페 | - | 10:08 |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=14.5분, total=14분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=14.5분, total=14분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=16.9분, total=17분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=48.1분, total=48분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=48.1분, total=48분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=50.1분, total=50분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=24.6분, total=25분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=24.0분, total=24분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=24.6분, total=25분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=14, ai=14, priority=17
- ✅ A(shortest<=ai,priority) [walk] — shortest=48, ai=48, priority=50
- ✅ A(shortest<=ai,priority) [transit] — shortest=24, ai=25, priority=25
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[1, 2]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[1, 2]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[1, 2]
- ✅ D(도보>=자동차 시간, 고정순서) — car=17분, walk=50분, transit=25분
- ✅ E(약속위반 0) [car/ai] — 위반=[]
- ✅ E(약속위반 0) [car/priority] — 위반=[]

## 시나리오 7 — 출발: 인천국제공항

- 일정: "송도 센트럴파크 구경하고 오후 1시 차이나타운에서 점심"
- 출발 시각: `11:30`
- 추출 장소(실추출): 송도 센트럴파크(P3), 차이나타운(P3, 약속 13:00)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 69 | 32.0 | 인천국제공항 → 차이나타운 → 송도 센트럴파크 | - | 13:20 |
| car | shortest | 69 | 32.0 | 인천국제공항 → 차이나타운 → 송도 센트럴파크 | - | 13:20 |
| car | priority | 69 | 32.0 | 인천국제공항 → 차이나타운 → 송도 센트럴파크 | - | 13:20 |
| walk | ai | 390 | 29.3 | 인천국제공항 → 차이나타운 → 송도 센트럴파크 | 차이나타운 | 18:00 |
| walk | shortest | 390 | 29.3 | 인천국제공항 → 차이나타운 → 송도 센트럴파크 | 차이나타운 | 18:00 |
| walk | priority | 390 | 29.3 | 인천국제공항 → 차이나타운 → 송도 센트럴파크 | 차이나타운 | 18:00 |
| transit | ai | 124 | 34.1 | 인천국제공항 → 차이나타운 → 송도 센트럴파크 | - | 13:49 |
| transit | shortest | 124 | 34.1 | 인천국제공항 → 차이나타운 → 송도 센트럴파크 | - | 13:49 |
| transit | priority | 124 | 34.1 | 인천국제공항 → 차이나타운 → 송도 센트럴파크 | - | 13:49 |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=68.9분, total=69분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=68.9분, total=69분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=68.9분, total=69분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=390.2분, total=390분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=390.2분, total=390분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=390.2분, total=390분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=123.8분, total=124분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=123.8분, total=124분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=123.8분, total=124분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=69, ai=69, priority=69
- ✅ A(shortest<=ai,priority) [walk] — shortest=390, ai=390, priority=390
- ✅ A(shortest<=ai,priority) [transit] — shortest=124, ai=124, priority=124
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[3, 3]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[3, 3]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[3, 3]
- ✅ D(도보>=자동차 시간, 고정순서) — car=80분, walk=433분, transit=136분
- ✅ E(약속위반 0) [car/ai] — 위반=[]
- ✅ E(약속위반 0) [car/priority] — 위반=[]

## 시나리오 8 — 출발: 대전역

- 일정: "성심당 들러 빵 사고 한밭수목원 산책"
- 출발 시각: 빈칸→현재시각 `04:24`
- 추출 장소(실추출): 성심당(P3), 한밭수목원(P4)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 22 | 9.9 | 대전역 → 성심당 → 한밭수목원 | - | 04:46 |
| car | shortest | 22 | 9.9 | 대전역 → 성심당 → 한밭수목원 | - | 04:46 |
| car | priority | 22 | 9.9 | 대전역 → 성심당 → 한밭수목원 | - | 04:46 |
| walk | ai | 107 | 8.0 | 대전역 → 성심당 → 한밭수목원 | - | 06:11 |
| walk | shortest | 107 | 8.0 | 대전역 → 성심당 → 한밭수목원 | - | 06:11 |
| walk | priority | 107 | 8.0 | 대전역 → 성심당 → 한밭수목원 | - | 06:11 |
| transit | ai | 41 | 9.4 | 대전역 → 성심당 → 한밭수목원 | - | 05:05 |
| transit | shortest | 41 | 9.4 | 대전역 → 성심당 → 한밭수목원 | - | 05:05 |
| transit | priority | 41 | 9.4 | 대전역 → 성심당 → 한밭수목원 | - | 05:05 |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=21.7분, total=22분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=21.7분, total=22분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=21.7분, total=22분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=107.1분, total=107분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=107.1분, total=107분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=107.1분, total=107분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=41.3분, total=41분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=41.3분, total=41분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=41.3분, total=41분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=22, ai=22, priority=22
- ✅ A(shortest<=ai,priority) [walk] — shortest=107, ai=107, priority=107
- ✅ A(shortest<=ai,priority) [transit] — shortest=41, ai=41, priority=41
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[3, 4]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[3, 4]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[3, 4]
- ✅ D(도보>=자동차 시간, 고정순서) — car=22분, walk=107분, transit=41분

## 시나리오 9 — 출발: 서울역

- 일정: "명동 쇼핑하고 남대문시장 구경, 서울로7017 산책, 덕수궁 관람"
- 출발 시각: `13:00`
- 추출 장소(실추출): 명동(P3), 남대문시장(P3), 서울로7017(P4), 덕수궁(P3)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 19 | 6.5 | 서울역 → 명동 → 남대문시장 → 서울로7017 → 덕수궁 | - | 13:19 |
| car | shortest | 19 | 6.5 | 서울역 → 명동 → 남대문시장 → 서울로7017 → 덕수궁 | - | 13:19 |
| car | priority | 21 | 7.3 | 서울역 → 덕수궁 → 명동 → 남대문시장 → 서울로7017 | - | 13:21 |
| walk | ai | 40 | 3.0 | 서울역 → 서울로7017 → 남대문시장 → 명동 → 덕수궁 | - | 13:40 |
| walk | shortest | 40 | 3.0 | 서울역 → 서울로7017 → 남대문시장 → 명동 → 덕수궁 | - | 13:40 |
| walk | priority | 53 | 4.0 | 서울역 → 덕수궁 → 명동 → 남대문시장 → 서울로7017 | - | 13:53 |
| transit | ai | 35 | 4.6 | 서울역 → 덕수궁 → 명동 → 남대문시장 → 서울로7017 | - | 13:35 |
| transit | shortest | 32 | 3.5 | 서울역 → 서울로7017 → 남대문시장 → 명동 → 덕수궁 | - | 13:32 |
| transit | priority | 35 | 4.6 | 서울역 → 덕수궁 → 명동 → 남대문시장 → 서울로7017 | - | 13:35 |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=18.6분, total=19분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=18.6분, total=19분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=21.5분, total=21분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=40.3분, total=40분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=40.3분, total=40분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=53.0분, total=53분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=35.4분, total=35분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=31.7분, total=32분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=35.4분, total=35분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=19, ai=19, priority=21
- ✅ A(shortest<=ai,priority) [walk] — shortest=40, ai=40, priority=53
- ✅ A(shortest<=ai,priority) [transit] — shortest=32, ai=35, priority=35
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[3, 3, 3, 4]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[3, 3, 3, 4]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[3, 3, 3, 4]
- ✅ D(도보>=자동차 시간, 고정순서) — car=19분, walk=56분, transit=36분

## 시나리오 10 — 출발: 청량리역

- 일정: "경동시장에서 장보고 서울약령시 구경"
- 출발 시각: 없음(시간제약 X)
- 추출 장소(실추출): 경동시장(P3), 서울약령시(P4)

| 이동수단 | 모드 | 총 이동시간(분) | 총 거리(km) | 방문 순서 | 약속위반 | 종료시각 |
|---|---|---|---|---|---|---|
| car | ai | 13 | 3.3 | 청량리역 → 경동시장 → 서울약령시 | - | - |
| car | shortest | 13 | 3.3 | 청량리역 → 경동시장 → 서울약령시 | - | - |
| car | priority | 13 | 3.3 | 청량리역 → 경동시장 → 서울약령시 | - | - |
| walk | ai | 15 | 1.2 | 청량리역 → 경동시장 → 서울약령시 | - | - |
| walk | shortest | 15 | 1.2 | 청량리역 → 경동시장 → 서울약령시 | - | - |
| walk | priority | 15 | 1.2 | 청량리역 → 경동시장 → 서울약령시 | - | - |
| transit | ai | 15 | 1.4 | 청량리역 → 경동시장 → 서울약령시 | - | - |
| transit | shortest | 15 | 1.4 | 청량리역 → 경동시장 → 서울약령시 | - | - |
| transit | priority | 15 | 1.4 | 청량리역 → 경동시장 → 서울약령시 | - | - |

검사 결과:
- ✅ C(legs>0 & 합정합) [car/ai] — leg합=12.6분, total=13분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/shortest] — leg합=12.6분, total=13분, 음수leg=False
- ✅ C(legs>0 & 합정합) [car/priority] — leg합=12.6분, total=13분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/ai] — leg합=15.4분, total=15분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/shortest] — leg합=15.4분, total=15분, 음수leg=False
- ✅ C(legs>0 & 합정합) [walk/priority] — leg합=15.4분, total=15분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/ai] — leg합=14.5분, total=15분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/shortest] — leg합=14.5분, total=15분, 음수leg=False
- ✅ C(legs>0 & 합정합) [transit/priority] — leg합=14.5분, total=15분, 음수leg=False
- ✅ A(shortest<=ai,priority) [car] — shortest=13, ai=13, priority=13
- ✅ A(shortest<=ai,priority) [walk] — shortest=15, ai=15, priority=15
- ✅ A(shortest<=ai,priority) [transit] — shortest=15, ai=15, priority=15
- ✅ B(priority 순서 비감소) [car] — 우선순위열=[3, 4]
- ✅ B(priority 순서 비감소) [walk] — 우선순위열=[3, 4]
- ✅ B(priority 순서 비감소) [transit] — 우선순위열=[3, 4]
- ✅ D(도보>=자동차 시간, 고정순서) — car=13분, walk=15분, transit=15분

---

## 부록: UI ↔ 백엔드 대조 검증 및 발견된 이슈

위 90건은 백엔드 API(=UI가 호출하는 바로 그 엔드포인트)를 직접 구동해 계산 로직을 전수 검증한 것이다.
추가로, 실제 웹 UI(브라우저)에서 계산한 값이 백엔드 결과와 일치하는지 시나리오 5(잠실역, 약속 없음)로 대조했다.

### 대조 방법
1. 브라우저에서 출발지 "잠실역" 검색 → 후보 "서울시설공단 잠실역 지하상가관리소" 선택.
2. 일정 "롯데월드타워 전망대 구경하고 석촌호수 산책" 입력 → AI 추출 → 장소 확정.
3. AI 추천 경로를 자동차 → 도보 순으로 이동수단을 바꿔가며 값 확인.
4. UI가 실제로 보낸 네트워크 요청(좌표 포함)을 캡처해, 같은 좌표로 백엔드 API를 재현.

### 결과
| 이동수단 | UI 표시값 | 백엔드(동일좌표 재현) | 판정 |
|---|---|---|---|
| 자동차 / AI | 3.4 km / 10분 (잠실역→석촌호수→롯데월드타워) | 3.4 km / 10분 (동일 순서) | ✅ 일치 |
| 도보 / AI | **1.5 km / 20분 (잠실역→석촌호수→롯데월드타워)** | **1.0 km / 14분 (잠실역→롯데월드타워→석촌호수)** | ❌ 불일치 |

- 도보 최적 순서는 `롯데월드타워→석촌호수`(직선거리 기반)이나, UI는 자동차로 최적화된 순서
  `석촌호수→롯데월드타워`를 그대로 두고 도보 거리만 다시 계산해 1.0km가 아닌 1.5km를 보여줬다.

### 근본 원인 (프론트 버그)
`frontend/src/App.jsx`의 `handleTravelModeChange`가 이동수단 변경 시 **순서 재최적화(optimize-route)를
호출하지 않고**, 기존 순서(`currentRoute.locations`)에 대해 `fetchRealEta`로 ETA만 다시 계산한다.
게다가 `calculateRoute`의 결과 캐시가 `mode`(ai/shortest/priority)만 키로 사용하므로, 이후 같은 모드
카드를 다시 눌러도 이전 이동수단의 순서가 캐시로 반환된다.

```js
const handleTravelModeChange = async (newMode) => {
  if (newMode === travelMode) return;
  setTravelMode(newMode);
  if (currentRoute?.locations?.length > 1) {
    const result = await fetchRealEta(currentRoute.locations, newMode); // ← 기존 순서 유지, 재최적화 X
    ...
  }
};
```

### 영향 범위
- 최적 순서가 이동수단별로 동일한 경우(본 리포트의 다수 시나리오)는 표시값이 정확하다.
- 최적 순서가 이동수단별로 달라지는 경우(예: 시나리오 5, 9의 도보)는 UI가 준최적 순서를 보여준다.
  거리/시간 숫자 자체는 "표시된 순서"에 대해서는 정확하다(엔진 오류 아님).

### 권장 수정 방향
- 이동수단 변경 시 `routeResults` 캐시를 비우고, 현재 활성 모드를 **새 이동수단으로 재최적화**
  (`calculateRoute(activeRoute, { force:true })`)하도록 변경. 단 `setTravelMode`가 비동기이므로
  `calculateRoute`가 새 이동수단 값을 쓰도록 인자로 넘기거나 `useEffect([travelMode])`로 처리한다.
- 또는 캐시 키를 `mode`가 아니라 `mode + travelMode` 조합으로 바꾼다.

### 결론
- **백엔드 경로 계산 엔진: 정상(전 항목 통과).**
- **프론트: 이동수단 전환 후 순서 재최적화 누락 버그 1건.** 위 방향으로 수정 필요.

### 수정 완료 (후속)
`handleTravelModeChange`가 이동수단 변경 시 캐시를 비우고 활성 모드를 새 이동수단으로 재최적화하도록
수정(A안). `calculateRoute`에 `travelModeArg` 인자를 추가해 비동기 `setTravelMode` 이전 값 문제를 회피.
브라우저 재검증: 잠실역 도보/AI = **1.0km/14분(롯데월드타워→석촌호수)** 로 정상화, 자동차↔도보 왕복 시
각각 올바른 순서로 재최적화됨(회귀 없음).
