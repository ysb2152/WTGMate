# WTGMate — Smart Route Planner

자연어로 적은 하루 일정을 **방문 장소로 추출·검증**하고, **우선순위·약속시각·이동수단**을 반영해
**경로를 최적화·비교**하고 지도에 **실제 경로**로 그려주는 웹앱.

[![docker-build](https://github.com/ysb2152/WTGMate/actions/workflows/docker-build.yml/badge.svg)](https://github.com/ysb2152/WTGMate/actions/workflows/docker-build.yml)

> 🔴 **라이브 데모: https://wtg-mate.vercel.app**
> 프론트는 Vercel에 상시 배포돼 있고, 백엔드/LLM은 비용 절감을 위해 개발 PC에서 Cloudflare Tunnel로
> 노출한다(데모 실행 중일 때만 백엔드 기능 동작). 설계·운영 방식은 [DEPLOY.md](DEPLOY.md) ·
> [deploy/CLOUDFLARE.md](deploy/CLOUDFLARE.md) 참고.

![WTGMate — 자연어 일정으로 계산한 AI 추천 경로](docs/screenshot.png)

---

## 무엇을 푸는가

- 여러 곳을 들르는 하루 일정에서 **방문 순서를 사람이 직접 고민**해야 하는 불편함
- "가장 가까운 곳부터"가 항상 좋은 게 아니다 — **일정의 중요도**와 **약속 시각**을 반영해야 한다
- **AI 추천 / 최단시간 / 내 우선순위** 경로를 나란히 **비교**하고 싶다

## 핵심 기능

- **자연어 → 장소 추출**: "오후 2시 광화문에서 회의, 명동에서 점심, 강남역에서 저녁" → 장소·할일·중요도·약속시각 추출
  - **로컬 파인튜닝 LLM**이 의미를 뽑고, 좌표는 **Kakao Local**로 다시 조회해 검증(모델의 좌표를 믿지 않음)
- **세 가지 최적화 모드**(각각 목적함수가 실제로 다름)
  - `최단시간`: 순수 이동시간 최소화
  - `내 우선순위`: 중요도 그룹 순서를 절대 기준으로 강제(그룹 내에서만 이동시간 최소) — DP로 정확 계산
  - `AI 추천`: 이동시간 + (중요도 × 방문순번) 페널티 균형
- **시간 제약 스케줄링**: 약속시각(자동추출)·체류시간 기반 도착시각 계산, 지각 경고, **"몇 시에 나가야 하나" 역산 추천**
- **실제 경로 표시**(직선이 아닌 실도로/인도/대중교통)
  - 자동차 = Kakao Mobility · 도보 = Tmap 보행자 · 대중교통 = ODsay
  - 무료 쿼터가 적어, **순서 최적화는 직선거리 추정으로, 실 API는 확정된 최종 경로에만**(n-1회) 호출 + leg 캐시(LRU·TTL)

## 아키텍처

```
브라우저 ──▶ React + Vite (Kakao Maps JS SDK)
                 │  /api
                 ▼
           FastAPI (Python)
             ├─ 장소 추출: 로컬 파인튜닝 LLM (Ollama)  → 좌표는 Kakao Local로 검증
             ├─ 최적화: 완전탐색(≤9곳) / OR-Tools(그 이상)
             └─ 실제 경로/시간: Kakao Mobility(자동차) · Tmap(도보) · ODsay(대중교통)
```

## 기술 스택

| 영역 | 사용 |
|---|---|
| Frontend | React, Vite, Kakao Maps JS SDK |
| Backend | FastAPI, OR-Tools |
| LLM | Qwen2.5-3B QLoRA 파인튜닝 → GGUF → Ollama(`wtgmate-parser`) |
| 지도/경로 API | Kakao Local·Mobility, Tmap 보행자, ODsay 대중교통 |
| 배포 | Docker / docker-compose, nginx (AWS 준비 완료) · Cloudflare Tunnel + Vercel (라이브) |

## 로컬 실행

**백엔드**
```bash
cd backend
python -m venv venv && venv\Scripts\pip install -r requirements.txt   # (Windows)
cp .env.example .env   # 키 채우기 (KAKAO_REST_API_KEY 필수, TMAP/ODSAY는 선택)
venv\Scripts\python -m uvicorn main:app --port 8000
```
LLM(장소 추출)을 쓰려면 [Ollama](https://ollama.com) 설치 후 GGUF로 모델 등록:
`ollama create wtgmate-parser -f backend/finetune/Modelfile` (GGUF는 Colab 노트북으로 생성 — git 제외)

**프론트엔드** (Kakao JS 키 도메인 때문에 5173 포트)
```bash
cd frontend
npm install
cp .env.example .env   # VITE_KAKAO_JAVASCRIPT_KEY 채우기
npm run dev
```

## 배포

- **Docker로 한 번에**: 루트에서 `docker compose up -d --build` (ollama + 모델 자동등록 + backend). 상세 [DEPLOY.md](DEPLOY.md)
- **AWS EC2**: nginx + certbot(HTTPS) 런북 [DEPLOY.md](DEPLOY.md)
- **비용 0 라이브**: Cloudflare Tunnel + Vercel [deploy/CLOUDFLARE.md](deploy/CLOUDFLARE.md)

## 개발 기록

기능마다의 **고민 · 선택 · 이유 · 어려웠던 점**을 리빙 문서로 남겼다 → [DEVELOPMENT_JOURNEY.md](DEVELOPMENT_JOURNEY.md)
(Gemini→로컬 모델 전환, 세 모드 목적함수 분리, 시간창 제약, 3종 실경로, 쿼터 최적화, 배포까지)
