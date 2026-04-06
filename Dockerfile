# =============================================================================
# fashionsearch — Dockerfile
#
# Stages disponibles:
#   base          → backend sin browser scraper  (~200 MB)
#   browser       → backend + Chrome + Selenium  (~700 MB)
#   frontend-dev  → Vite dev server (hot reload)
#   frontend-prod → build estático listo para nginx
#
# Uso directo:
#   docker build --target base         -t fashionsearch-backend:base    .
#   docker build --target browser      -t fashionsearch-backend:browser .
#   docker build --target frontend-prod -t fashionsearch-frontend        .
#
# Uso normal: docker compose up (ver docker-compose.yml)
# =============================================================================


# =============================================================================
# BACKEND — Stage 1: base
# Python + requests + BS4. Sin browser. Imagen liviana.
# =============================================================================
FROM python:3.12-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    # lxml necesita estas libs en tiempo de ejecución
    libxml2 \
    libxslt1.1 \
    # curl para el healthcheck del compose
    curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencias Python primero → mejor cache de capas Docker.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código fuente.
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]


# =============================================================================
# BACKEND — Stage 2: browser
# Extiende base con Chrome headless + Selenium.
# Chrome se instala desde apt (Chromium de Debian) para evitar
# que webdriver-manager descargue binarios en runtime.
# =============================================================================
FROM base AS browser

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    fonts-noto-color-emoji \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-browser.txt .
RUN pip install --no-cache-dir -r requirements-browser.txt

# Apuntar al chromedriver del sistema → evita descargas en runtime.
ENV WDM_LOCAL=1 \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_BIN=/usr/bin/chromium \
    USE_BROWSER_SCRAPER=true


# =============================================================================
# FRONTEND — Stage 3: frontend-dev
# Servidor Vite con hot reload. Solo para desarrollo local con Docker.
# Se levanta con: docker compose --profile dev up
# =============================================================================
FROM node:20-slim AS frontend-dev

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

# En el compose el src se monta como volumen (hot reload).
# Este COPY cubre el caso de build directo con docker build.
COPY . .

EXPOSE 5173

# --host 0.0.0.0 es necesario para que Vite sea accesible fuera del contenedor.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]


# =============================================================================
# FRONTEND — Stage 4: frontend-prod (builder intermedio)
# Compila el SPA. El resultado lo copia el stage final.
# =============================================================================
FROM node:20-slim AS frontend-builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# VITE_API_BASE se puede sobreescribir en CI/CD para apuntar a otro dominio.
# En producción con nginx en el mismo compose, /api es suficiente (proxy local).
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=${VITE_API_BASE}

RUN npm run build


# =============================================================================
# FRONTEND — Stage 5: frontend-prod (imagen final)
# Solo nginx + archivos estáticos. Sin Node, sin código fuente.
# =============================================================================
FROM nginx:1.27-alpine AS frontend-prod

# Configuración de nginx: sirve el SPA y proxea /api al backend.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Archivos del build generados en el stage anterior.
COPY --from=frontend-builder /app/dist /usr/share/nginx/html

EXPOSE 80
