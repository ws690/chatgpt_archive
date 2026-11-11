FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BACKUP_ROOT=/data TZ=Europe/London
WORKDIR /app
COPY app/ /app/
RUN chmod 755 /app && chmod 644 /app/*.py
USER 1000:100
CMD ["python", "/app/scheduler.py"]
