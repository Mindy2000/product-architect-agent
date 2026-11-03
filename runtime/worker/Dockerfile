FROM mcr.microsoft.com/playwright:v1.64.1-noble
WORKDIR /opt/worker
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund && chown -R pwuser:pwuser /opt/worker
USER pwuser
ENV HOME=/tmp/worker-home
ENV CI=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
