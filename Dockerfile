# Multi-stage Dockerfile for Rails to Spinel AOT binary
# Stage 1: Build environment with Ruby 3.4.5 and native toolchain
FROM ruby:3.4.5-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV CC=clang

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      clang \
      make \
      libsqlite3-dev \
      libjemalloc-dev \
      libssl-dev \
      zlib1g-dev \
      sqlite3 \
      pkg-config \
      xz-utils \
      python3 \
      nodejs \
      npm && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY . /workspace

# Install bundle dependencies, install pinned Roundhouse and Spinel, then transpile and build native binary
RUN (cd blog && bundle install) && \
    bash scripts/install-toolchain.sh && \
    bash scripts/build.sh

# Stage 2: Minimal runtime image without Ruby, Rails, or Spinel
FROM ubuntu:24.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PORT=3000
ENV SPINEL_WORKERS=2

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      sqlite3 \
      libsqlite3-0 \
      libjemalloc2 \
      curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy runtime bundle (compiled binary, static assets, seed sql, configs)
COPY --from=builder /workspace/out/runtime /app
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh /app/blog

EXPOSE 3000
VOLUME ["/app/storage"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]
