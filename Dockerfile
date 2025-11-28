FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
COPY bot.py .

RUN pip install --no-cache-dir -r requirements.txt

ENV TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y tzdata \
    && rm -rf /var/lib/apt/lists/*

CMD ["python", "bot.py"]

# mount volumes for config files and logs
# VOLUME /app/logs
# VOLUME /app/config.json
# VOLUME /app/google-translate-credential.json