FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY static static
# Demo state lives in a local SQLite file. Cloud Run resets it on a new instance,
# which suits a public demo because every visitor starts from clean sample data.
ENV CALMY_DB=/tmp/calmy.db
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
