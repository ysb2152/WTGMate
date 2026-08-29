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

## Phase 1 — 배포 전 코드 정리  ✅ 대부분 완료

| 항목 | 상태 | 내용 | 위치 |
|---|---|---|---|
| 프론트 API 주소 | ✅ | `VITE_API_BASE_URL` env로 주입, 미설정 시 로컬 폴백. 빈 문자열("")이면 `/api` 상대경로(nginx 동일 오리진용) | `frontend/src/App.jsx:3` |
| CORS | ✅ | `ALLOWED_ORIGINS` env(콤마 구분), 미설정 시 `*`. 자격증명 미사용이라 `allow_credentials=False` | `backend/main.py` |
| Ollama 호스트 | ✅ | `OLLAMA_HOST`/`OLLAMA_MODEL` env | `backend/main.py` |
| 의존성 고정 | ✅ | `backend/requirements.txt` 생성(fastapi·uvicorn[standard]·httpx·python-dotenv·pydantic·ortools) | `backend/requirements.txt` |
| env 템플릿 | ✅ | `backend/.env.example`, `frontend/.env.example` 추가 | - |
| 컨테이너화 | ✅ | `backend/Dockerfile`(+`.dockerignore`), 루트 `docker-compose.yml`(ollama + model-loader + backend) | - |
| 모델 등록 | ✅ | `backend/finetune/Modelfile`(raw passthrough) 커밋 → `ollama create`로 재현 | - |
| nginx | ✅ | `deploy/nginx-wtgmate.conf`(정적 프론트 + /api 프록시, certbot용) | - |

**→ 배포 산출물이 모두 준비됨. EC2에 올려 아래 "배포 순서 요약"만 실행하면 기동된다.**

**배포 시 주입할 env 요약**
- 백엔드: `KAKAO_REST_API_KEY`, `TMAP_APP_KEY`, `ODSAY_API_KEY`, `ALLOWED_ORIGINS`(배포 도메인), `OLLAMA_HOST`/`OLLAMA_MODEL`
- 프론트(빌드 시점): `VITE_KAKAO_JAVASCRIPT_KEY`, `VITE_API_BASE_URL`(nginx 동일 오리진이면 "")

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

## 배포 순서 요약 (산출물 준비 완료 → EC2에서 실행만)

**로컬(완료됨):** Phase 1 코드 정리 + Dockerfile/compose/nginx/Modelfile 산출물. GGUF는 Colab 노트북으로 생성.

**EC2(Ubuntu, t3.medium+)에서:**
```bash
# 0) 도구
sudo apt update && sudo apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx
sudo usermod -aG docker $USER   # 재로그인

# 1) 코드 + 모델
git clone https://github.com/ysb2152/WTGMate.git && cd WTGMate
mkdir -p model
aws s3 cp s3://<버킷>/wtgmate-parser.gguf model/wtgmate-parser.gguf   # GGUF 전달(S3)
cp backend/finetune/Modelfile model/Modelfile

# 2) 키 주입(.env 또는 SSM). ALLOWED_ORIGINS는 배포 도메인.
cat > .env <<EOF
KAKAO_REST_API_KEY=...
TMAP_APP_KEY=...
ODSAY_API_KEY=...
ALLOWED_ORIGINS=https://YOUR_DOMAIN
EOF

# 3) 백엔드 + Ollama 기동(모델 자동 등록) → 헬스체크
docker compose up -d --build
curl http://localhost:8000/api/health          # 3개 키 configured 확인

# 4) 프론트 빌드 → nginx 정적 서빙
cd frontend && VITE_API_BASE_URL="" VITE_KAKAO_JAVASCRIPT_KEY=... npm ci && npm run build
sudo mkdir -p /var/www/wtgmate && sudo cp -r dist/* /var/www/wtgmate/ && cd ..
sudo cp deploy/nginx-wtgmate.conf /etc/nginx/sites-available/wtgmate
sudo sed -i 's/YOUR_DOMAIN/실제도메인/' /etc/nginx/sites-available/wtgmate
sudo ln -sf /etc/nginx/sites-available/wtgmate /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 5) HTTPS
sudo certbot --nginx -d YOUR_DOMAIN
```

**콘솔 작업:** 보안그룹 80/443 오픈, Elastic IP + 도메인 A레코드, **Kakao JS 키 플랫폼에 배포 도메인 등록**, **ODsay '서버' 앱에 EC2 공인 IP 등록**.

---

## 실제 라이브 데모는 Cloudflare Tunnel로 운영 (비용 절감)

위 AWS 구성은 **배포 즉시 가능한 상태로 완비**해 두었으나(t3.medium 상시 ~$30/월), 포트폴리오 데모는
비용 0의 **Cloudflare Tunnel + Vercel** 로 운영한다. 백엔드/Ollama는 개발 PC에서 그대로 돌고
(모든 API 키가 이미 그 IP로 동작), Cloudflare Tunnel이 HTTPS로 노출한다. 상세는 `deploy/CLOUDFLARE.md`.
