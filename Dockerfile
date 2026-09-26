FROM python:3.11-slim

# Install Docker CLI for sandbox verification (Docker-in-Docker)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        docker.io \
        git \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Copy and install dependencies
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Entry point
ENTRYPOINT ["python", "-m", "reviewpilot.main"]
