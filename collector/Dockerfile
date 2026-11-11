FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
USER root
RUN pip install --no-cache-dir playwright==1.64.1 && apt-get update && apt-get install -y --no-install-recommends xvfb x11vnc && rm -rf /var/lib/apt/lists/*
WORKDIR /collector
COPY collector.py health.py auth.sh /collector/
RUN chmod 755 /collector /collector/auth.sh && chmod 644 /collector/*.py
USER pwuser
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
CMD ["python", "/collector/collector.py", "schedule"]
