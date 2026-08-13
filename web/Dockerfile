FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      python3 \
      python3-cups \
      python3-flask \
      tzdata \
      curl \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /data
WORKDIR /app
COPY app.py /app/app.py
COPY templates /app/templates
EXPOSE 8080
CMD ["python3", "-u", "/app/app.py"]
