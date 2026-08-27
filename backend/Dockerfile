# WTGMate FastAPI 백엔드 이미지.
# Ollama는 이 이미지에 넣지 않고 별도 컨테이너로 띄운다(docker-compose.yml 참고).
FROM python:3.11-slim

WORKDIR /app

# httpx의 TLS 검증용 CA 등 최소 런타임 의존성.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 설치(레이어 캐시 활용).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드(.dockerignore로 venv/데이터/gguf/.env 제외).
COPY . .

EXPOSE 8000

# 프로덕션 기동. OLLAMA_HOST/키 등은 compose가 환경변수로 주입한다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
