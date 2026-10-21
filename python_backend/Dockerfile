FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 REMASK_HTTP_ONLY=1
WORKDIR /app
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements.lock /app/
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt
COPY . /app
RUN python -m compileall -q /app \
    && python -c "from app.facebook_business_browser import FacebookBusinessBrowser; from app.session import ProfileSession; assert callable(ProfileSession.facebook_business_browser)"
CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
