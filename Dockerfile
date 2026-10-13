FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY static static
COPY data data
# Demo state lives in a local SQLite file. Cloud Run resets it on a new instance,
# which suits a public demo because every visitor starts from clean sample data.
ENV KODA_DB=/tmp/koda.db
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
