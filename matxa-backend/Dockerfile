FROM nvidia/cuda:12.6.3-cudnn9-runtime-ubuntu22.04

ARG MATXA_UPSTREAM_REPO=https://github.com/langtech-bsc/minimal-tts-api.git
ARG MATXA_UPSTREAM_REF=b0084b203100b83ace8dfd2fde09fd18eb875e18
ARG MATXA_RUNTIME=cuda

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/huggingface \
    HUGGINGFACE_HUB_CACHE=/cache/huggingface/hub \
    MATXA_MODEL_DIR=/cache/models \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf \
    automake \
    build-essential \
    ca-certificates \
    cmake \
    ffmpeg \
    git \
    libtool \
    patch \
    pkg-config \
    python3 \
    python3-dev \
    python3-pip \
  && ln -sf /usr/bin/python3 /usr/local/bin/python3 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/build

RUN git clone --depth 1 "${MATXA_UPSTREAM_REPO}" upstream \
  && cd upstream \
  && git fetch --depth 1 origin "${MATXA_UPSTREAM_REF}" \
  && git checkout "${MATXA_UPSTREAM_REF}"

WORKDIR /opt/build/upstream

RUN git clone --depth 1 -b dev-ca https://github.com/projecte-aina/espeak-ng \
  && cd espeak-ng \
  && ./autogen.sh \
  && ./configure --prefix=/usr \
  && make \
  && make install

COPY matxa-backend/patches/0001-enable-cuda-provider.patch /tmp/0001-enable-cuda-provider.patch

RUN if [ "${MATXA_RUNTIME}" = "cuda" ]; then patch -p1 < /tmp/0001-enable-cuda-provider.patch; fi

RUN python3 -m pip install --no-cache-dir --upgrade pip \
  && grep -vE '^onnxruntime(-gpu)?($|[<>=!~].*)' requirements.txt > /tmp/matxa-requirements.txt \
  && python3 -m pip install --no-cache-dir -r /tmp/matxa-requirements.txt \
  && python3 -m pip install --no-cache-dir 'huggingface-hub[cli]' \
  && if [ "${MATXA_RUNTIME}" = "cuda" ]; then \
      python3 -m pip install --no-cache-dir onnxruntime-gpu; \
    else \
      python3 -m pip install --no-cache-dir onnxruntime; \
    fi

RUN useradd -m -u 1000 user \
  && mkdir -p /cache/models /cache/huggingface /app \
  && chown -R user:user /cache /app /opt/build/upstream

COPY matxa-backend/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

USER user
WORKDIR /opt/build/upstream

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
