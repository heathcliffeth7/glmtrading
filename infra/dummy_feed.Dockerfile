FROM python:3.12-slim

WORKDIR /app

COPY infra/dummy_feed.py /app/dummy_feed.py
RUN pip install redis influxdb-client pandas

CMD ["python", "dummy_feed.py"]
