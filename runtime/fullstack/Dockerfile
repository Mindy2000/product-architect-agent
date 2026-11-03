FROM product-architect-worker:1.0
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /opt/fullstack/requirements.lock
RUN python3 -m venv /opt/fullstack/venv && /opt/fullstack/venv/bin/pip install --no-cache-dir --no-deps -r /opt/fullstack/requirements.lock
ENV PATH=/opt/fullstack/venv/bin:$PATH
USER pwuser
