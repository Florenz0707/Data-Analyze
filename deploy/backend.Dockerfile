FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.24 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        default-libmysqlclient-dev \
        libgomp1 \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY backend/django_backend/pyproject.toml backend/django_backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/django_backend ./

CMD ["python", "manage.py"]
