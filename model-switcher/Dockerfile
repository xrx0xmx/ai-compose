FROM docker:27.5.1-cli

RUN apk add --no-cache python3 py3-pip

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY model-switcher/requirements.txt /app/requirements.txt
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r /app/requirements.txt
ENV PATH="/venv/bin:${PATH}"

COPY model-switcher/app.py /app/app.py
COPY model-switcher/static /app/static

EXPOSE 8081
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]
