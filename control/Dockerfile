FROM python:3.11-slim

WORKDIR /app

COPY control/requirements.txt /app/control/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/control/requirements.txt

COPY control/app.py /app/control/app.py
COPY control/entrypoint.sh /app/entrypoint.sh

COPY litellm-config.qwen-fast.yml /opt/model-configs/qwen-fast.yml
COPY litellm-config.qwen-quality.yml /opt/model-configs/qwen-quality.yml
COPY litellm-config.deepseek.yml /opt/model-configs/deepseek.yml
COPY litellm-config.qwen-max.yml /opt/model-configs/qwen-max.yml

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
