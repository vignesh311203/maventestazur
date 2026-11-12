FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/index.html ./
COPY frontend/src ./src
ENV VITE_API_URL=""
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY --from=frontend /build/dist ./web
RUN useradd --create-home appuser
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.website:app --host 0.0.0.0 --port ${PORT:-8000} --no-proxy-headers"]
