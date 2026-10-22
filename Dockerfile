# Python side: mock portals (default) and the eval runner.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY agentgate ./agentgate
RUN pip install --no-cache-dir . && python -m playwright install --with-deps chromium
EXPOSE 5055
CMD ["python", "-m", "agentgate", "portals", "--host", "0.0.0.0", "--port", "5055"]
