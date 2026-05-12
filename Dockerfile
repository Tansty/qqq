FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV QQQ_ADVISOR_HOST=0.0.0.0
ENV QQQ_ADVISOR_PORT=8765
ENV QQQ_ADVISOR_CONFIG=/app/storage/config.json
ENV QQQ_ADVISOR_DATA_DIR=/app/storage/data

WORKDIR /app

COPY qqq_advisor.py qqq_agent.py web_server.py start_web.sh ./
COPY static ./static

RUN mkdir -p /app/storage/data

EXPOSE 8765

CMD ["python3", "web_server.py"]
