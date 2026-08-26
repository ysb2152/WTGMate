# 배포 가이드 (AWS, 상시 라이브 데모)

이 프로젝트를 AWS에 올려 **상시 접근 가능한 데모 URL**로 배포하기 위한 계획.
포트폴리오 목적이므로 대규모 트래픽/오토스케일링은 고려하지 않는다.

## 아키텍처 개요

```
브라우저 ──https──▶ nginx (EC2)
                     ├─ 정적 프론트(Vite build) 서빙
                     └─ /api/* ──▶ FastAPI(backend) ──▶ Ollama(로컬 3B 모델)
                                                    └──▶ Kakao 로컬/모빌리티 API
```

단일 EC2 인스턴스에 nginx + backend + Ollama를 함께 올린다.
ALB(월 ~$16)와 API Gateway(요청 29초 하드 리밋)를 모두 피하기 위해 nginx 리버스 프록시로 처리한다.

---

## ⚠️ 반드시 짚어야 할 함정

1. **HTTPS 필수 (mixed-content 차단)**
   - 프론트를 https로 서빙하면 백엔드 API도 반드시 https여야 한다 (http면 브라우저가 차단).
   - Kakao 지도 SDK도 https 도메인을 요구한다.
   - → nginx + Let's Encrypt(certbot) 무료 인증서로 해결.

2. **`backend/requirements.txt` 필요**
   - 현재 백엔드 의존성이 어디에도 고정돼 있지 않다. 새 서버에서 `pip install`할 목록 자체가 없으므로 배포 전 생성 필수.

3. **모델 가중치(GGUF ~1.8GB)는 git에 없음**
   - `.gitignore`로 제외돼 있다. 별도 전달 경로(S3) 필요.

---

## Phase 1 — 배포 전 코드 정리

| 항목 | 현재 | 바꿀 것 | 위치 |
|---|---|---|---|
| 프론트 API 주소 | `http://127.0.0.1:8000` 하드코딩 | `VITE_API_BASE_URL` 환경변수 (fallback 유지) | `frontend/src/App.jsx` |
| CORS | `allow_origins=["*"]` | 배포 도메인만 허용 (env로 주입) | `backend/main.py` |
| Ollama 호스트 | `OLLAMA_HOST` env ✅ | 이미 됨 | `backend/main.py` |
| 의존성 고정 | 없음 | `backend/requirements.txt` 생성 | - |
| 컨테이너화 | 없음 | `Dockerfile` + `docker-compose.yml` (backend + ollama) | - |

## Phase 2 — 모델(GGUF) 전달

- GGUF를 **S3 버킷**에 업로드.
- 인스턴스 기동 스크립트(user-data 또는 배포 스크립트)에서 S3에서 내려받아 `ollama create wtgmate-parser -f Modelfile` 실행.
- 대안: Docker 이미지에 굽기(이미지가 커지는 단점).

## Phase 3 — AWS 인프라

- **EC2 t3.medium(4GB) 이상** — 3B 모델이 RAM 2~3GB를 점유하므로 프리티어 micro(1GB)로는 불가.
- **단일 인스턴스 + nginx**:
  - 정적 프론트 서빙 + `/api` 리버스 프록시.
  - ALB / API Gateway 불필요.
- **TLS**: nginx + Let's Encrypt(certbot) 무료 인증서.
- **도메인**: Route53 또는 기존 도메인 → Elastic IP 연결.
- **Kakao 콘솔**: 배포 도메인을 JS 키 플랫폼에 등록 (누락 시 지도 안 뜸). REST 키에 IP 허용목록을 쓰면 EC2 IP도 등록.

## Phase 4 — 비용 관리

- 데모하지 않을 땐 **인스턴스 중지** → 컴퓨팅 비용 0 (EBS/EIP 소액만 과금).
- t3.medium 상시 가동이라도 월 ~$30 수준.

## Phase 5 — Secrets

- `.env` 평문 대신 **AWS SSM Parameter Store**(무료) 권장.
- 현재 `.env`는 `.gitignore`로 제외돼 있어 커밋되지 않음 (유지).

---

## 배포 순서 요약

1. Phase 1 코드 정리 (로컬에서 완료 가능)
2. GGUF S3 업로드
3. EC2(t3.medium) 프로비저닝 + 보안 그룹(80/443) 설정
4. Docker/의존성 설치 → backend + Ollama 기동 → `ollama create`
5. nginx 설정(정적 서빙 + /api 프록시) + certbot HTTPS
6. 도메인 연결 + Kakao 콘솔에 도메인 등록
7. 헬스체크(`/api/health`)로 확인
