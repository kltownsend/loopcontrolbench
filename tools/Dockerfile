# Base image for the sandboxed runner. python:3.13-slim plus git and a compiler, so repos
# that use setuptools_scm or ship C extensions install cleanly. Slim keeps the pull small.
# Build once:
#   docker build -t loopcontrolbench-base tools/
FROM python:3.13-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*
# The allowlist egress proxy runs from this (trusted) image, never from an untrusted repo.
COPY allowlist_proxy.py /allowlist_proxy.py
