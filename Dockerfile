FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 bot && useradd --uid 10001 --gid bot --create-home bot
COPY --chown=bot:bot just_bot ./just_bot
COPY --chown=bot:bot extensions ./extensions
RUN mkdir /app/data && chown bot:bot /app/data
USER bot
CMD ["python", "-m", "just_bot"]
