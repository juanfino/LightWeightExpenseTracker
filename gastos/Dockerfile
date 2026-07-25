FROM python:3.11-alpine

RUN apk add --no-cache gcc musl-dev libffi-dev postgresql17-client

WORKDIR /app
COPY gastos/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gastos/app/ /app/app/
COPY gastos/alembic.ini /app/alembic.ini
COPY gastos/migrations/ /app/migrations/

ENV DATABASE_URL=postgresql://gastos@127.0.0.1:5432/gastos
ENV PYTHONPATH=/app/app

CMD ["python3", "/app/app/main.py"]
