# 실제 라이브 데모 — Cloudflare Tunnel + Vercel (비용 0)

AWS 배포 산출물은 완비돼 있으나(`DEPLOY.md`, ~$30/월), 라이브 데모는 비용 0의 이 구성으로 운영한다.

```
브라우저 ─https─▶ Vercel(정적 프론트, 무료·HTTPS)
                     └─ /api ─https─▶ Cloudflare Tunnel ─▶ 내 PC: FastAPI(8000) + Ollama
```

**왜 이게 유리한가**
- 백엔드/Ollama가 개발 PC에 그대로 있어, **모든 API 키가 이미 그 IP로 동작**한다(ODsay '서버' 앱 IP도 이미 등록됨). 재설정 불필요.
- 모델(GGUF) 전송·EC2·nginx·certbot 전부 불필요. HTTPS는 Cloudflare가 처리.
- 단점: 데모 중에만 PC를 켜두면 됨(데모 실행 시에만 가동).

---

## 1. 백엔드 + 터널 실행 (내 PC)

```powershell
# 백엔드 (venv)
cd C:\Users\ysb21\WTGMate\backend
venv\Scripts\python.exe -m uvicorn main:app --port 8000
```
다른 터미널에서 터널:
```powershell
# (A) 빠른 테스트용 — 임시 URL(재시작마다 바뀜)
cloudflared tunnel --url http://localhost:8000
#   => https://<랜덤>.trycloudflare.com 출력. /api/health로 확인.

# (B) 안정 URL — named tunnel (Cloudflare 계정 + 본인 도메인 필요, 무료)
cloudflared tunnel login                       # 브라우저로 Cloudflare 로그인
cloudflared tunnel create wtgmate              # 터널 생성(자격증명 저장)
cloudflared tunnel route dns wtgmate api.내도메인.com
#   config.yml에 아래 작성:
#     tunnel: wtgmate
#     credentials-file: C:\Users\ysb21\.cloudflared\<UUID>.json
#     ingress:
#       - hostname: api.내도메인.com
#         service: http://localhost:8000
#       - service: http_status:404
cloudflared tunnel run wtgmate                 # https://api.내도메인.com 로 안정 노출
```
헬퍼: `deploy/run-demo.ps1` (백엔드+터널 동시 실행).

## 2. 프론트 배포 (Vercel, 무료)

```powershell
cd C:\Users\ysb21\WTGMate\frontend
npm i -g vercel        # 최초 1회
vercel login
vercel                 # 프로젝트 연결(빌드 명령/출력 dist 자동 감지)
```
Vercel 대시보드 > 프로젝트 > Settings > Environment Variables 에 주입 후 재배포:
- `VITE_KAKAO_JAVASCRIPT_KEY` = (Kakao JS 키)
- `VITE_API_BASE_URL` = `https://api.내도메인.com` (터널 URL. 임시 URL이면 바뀔 때마다 갱신)

CORS: 백엔드 `.env`의 `ALLOWED_ORIGINS`에 Vercel 도메인 추가:
```
ALLOWED_ORIGINS=https://wtgmate.vercel.app
```
(백엔드 재시작 필요)

## 3. Kakao 콘솔 등록 (필수)

- Kakao Developers > 내 앱 > 플랫폼 > Web 에 **Vercel 도메인**(`https://wtgmate.vercel.app`) 등록.
  → 누락 시 지도 SDK가 뜨지 않는다.

## 4. 확인

- `https://<터널>/api/health` → 200, 3개 키 configured.
- Vercel 프론트 접속 → 일정 입력 → 경로 계산까지 정상 동작.

---

## 상태 (2026-08-27)

- ✅ quick tunnel로 백엔드 HTTPS 노출 검증됨(`/api/health` 200, kakao/tmap/odsay 모두 configured).
- ⬜ 안정 URL(named tunnel, 본인 도메인) + Vercel 배포 + Kakao 도메인 등록 — 계정 작업이라 직접 진행.
