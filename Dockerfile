FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY pure /app/pure
COPY eval_cases.json /app/eval_cases.json
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

RUN pip install --no-cache-dir -e .

RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
