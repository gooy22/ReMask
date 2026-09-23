FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 REMASK_CHROMIUM_EXECUTABLE=/usr/bin/chromium
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
RUN python -m compileall -q /app \
    && python -c "import playwright; import shutil; assert shutil.which('chromium')" \
    && python -c "from app.facebook_business_browser import FacebookBusinessBrowser; from app.session import ProfileSession; assert callable(ProfileSession.facebook_business_browser)"
CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
