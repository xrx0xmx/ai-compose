FROM python:3.11-slim

WORKDIR /app

COPY matxa-adapter/requirements.txt /app/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/requirements.txt

COPY matxa-adapter/main.py /app/main.py

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
