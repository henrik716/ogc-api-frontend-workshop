FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# The image ships an apt source for Yarn whose signing key is missing, which
# makes every `apt-get update` fail, including the one the docker-in-docker
# feature runs. Codespaces then falls back to a bare recovery container.
# We don't use Yarn, so drop the source.
RUN grep -l yarnpkg /etc/apt/sources.list.d/* 2>/dev/null | xargs -r rm -f
