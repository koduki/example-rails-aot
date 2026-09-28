# Multi-stage DevContainer: Bundles CRuby, JRuby, Roundhouse, and Spinel in one unified container
# Stage 1: Pre-built JRuby 10.0.7.0 and Eclipse Temurin OpenJDK 21
FROM jruby:10.0.7.0-jdk21 AS jruby-source

# Stage 2: Main DevContainer based on CRuby 3.4.5 (Debian Bookworm)
FROM ruby:3.4.5-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV CC=clang

# 1. Install toolchain: Clang, SQLite3, jemalloc, Node.js, Python3, build tools
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      clang \
      make \
      pkg-config \
      xz-utils \
      sqlite3 \
      libsqlite3-dev \
      libjemalloc-dev \
      libjemalloc2 \
      libssl-dev \
      zlib1g-dev \
      python3 \
      python3-pip \
      nodejs \
      npm \
      procps \
      less \
      tree \
      vim \
      jq && \
    rm -rf /var/lib/apt/lists/*

# 2. Copy JDK 21 and JRuby 10.0.7.0 from jruby-source
COPY --from=jruby-source /opt/java/openjdk /opt/java/openjdk
COPY --from=jruby-source /opt/jruby /opt/jruby

ENV JAVA_HOME=/opt/java/openjdk
ENV JRUBY_HOME=/opt/jruby
ENV PATH="/opt/java/openjdk/bin:/usr/local/bundle/bin:/usr/local/bin:$PATH"

# Setup isolated JRuby gem environment so JRuby gems and CRuby gems never collide
RUN mkdir -p /opt/jruby-bundle/bin && \
    printf '#!/bin/bash\nexport GEM_HOME=/opt/jruby-bundle\nexport GEM_PATH=/opt/jruby-bundle:/opt/jruby/lib/ruby/gems/shared\nexport PATH=/opt/jruby-bundle/bin:$PATH\nexec /opt/jruby/bin/jruby "$@"\n' > /usr/local/bin/jruby && \
    chmod +x /usr/local/bin/jruby && \
    printf '#!/bin/bash\nexport GEM_HOME=/opt/jruby-bundle\nexport GEM_PATH=/opt/jruby-bundle:/opt/jruby/lib/ruby/gems/shared\nexport PATH=/opt/jruby-bundle/bin:$PATH\nexec /opt/jruby/bin/jruby -S gem "$@"\n' > /usr/local/bin/jgem && \
    chmod +x /usr/local/bin/jgem && \
    printf '#!/bin/bash\nexport GEM_HOME=/opt/jruby-bundle\nexport GEM_PATH=/opt/jruby-bundle:/opt/jruby/lib/ruby/gems/shared\nexport PATH=/opt/jruby-bundle/bin:$PATH\nexec /opt/jruby/bin/jruby -S bundle "$@"\n' > /usr/local/bin/jbundle && \
    chmod +x /usr/local/bin/jbundle

# Install Bundler 2.6.9 on CRuby and JRuby
RUN gem install bundler -v 2.6.9 --no-document && \
    /usr/local/bin/jgem install bundler -v 2.6.9 --no-document

# 3. Install Roundhouse (pinned v2026.9.18)
ARG ROUNDHOUSE_VERSION=v2026.9.18
ARG ROUNDHOUSE_SHA256=aa8b13f53154ad41cc0a83088f6d4f06532760ec8dbeb165eccc0c946bcd7945
RUN mkdir -p /tmp/roundhouse && \
    curl -fsSL --retry 3 "https://github.com/rubys/roundhouse/releases/download/${ROUNDHOUSE_VERSION}/roundhouse-x86_64-unknown-linux-musl.tar.xz" -o /tmp/roundhouse/roundhouse.tar.xz && \
    echo "${ROUNDHOUSE_SHA256}  /tmp/roundhouse/roundhouse.tar.xz" | sha256sum --check - && \
    tar -xJf /tmp/roundhouse/roundhouse.tar.xz -C /tmp/roundhouse && \
    RH_BIN="$(find /tmp/roundhouse -type f -name roundhouse | head -1)" && \
    install -m 755 "$RH_BIN" /usr/local/bin/roundhouse && \
    rm -rf /tmp/roundhouse

# 4. Install Spinel (pinned 2026.09.12)
ARG SPINEL_VERSION=2026.09.12
ARG SPINEL_SHA256=1bba269cfc8004bcd60b75471cd44eaccdc66eed60b1f636d1367c897c764a77
RUN mkdir -p /tmp/spinel && \
    curl -fsSL --retry 3 "https://github.com/matz/spinel/releases/download/${SPINEL_VERSION}/spinel-${SPINEL_VERSION}.tar.xz" -o /tmp/spinel/spinel.tar.xz && \
    echo "${SPINEL_SHA256}  /tmp/spinel/spinel.tar.xz" | sha256sum --check - && \
    tar -xJf /tmp/spinel/spinel.tar.xz -C /tmp/spinel --strip-components=1 && \
    make -C /tmp/spinel -j$(nproc) CC=clang && \
    make -C /tmp/spinel install PREFIX=/usr/local CC=clang && \
    rm -rf /tmp/spinel

WORKDIR /workspace

EXPOSE 3000 3001 3002

CMD ["bash"]
