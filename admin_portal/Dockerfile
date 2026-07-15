FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Also copy shared backend modules needed by the admin portal
COPY ../backend/storage/ ./backend/storage/

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/api/prompts/active')" || exit 1

CMD ["python", "app.py"]
