# WTGMate — Frontend

React + Vite로 만든 프론트엔드입니다. 프로젝트 전체 설명은 저장소 루트의 [README](../README.md)를 참고해 주세요.

## 개발 실행

```bash
npm install
cp .env.example .env   # VITE_KAKAO_JAVASCRIPT_KEY 입력
npm run dev            # http://localhost:5173
```

Kakao 지도 SDK가 도메인을 확인하므로 `http://localhost:5173`을 Kakao 콘솔의 JavaScript SDK 도메인에 등록해야 지도가 표시됩니다.
