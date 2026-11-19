FROM python:3.12-slim
RUN pip install --no-cache-dir --disable-pip-version-check "pytest>=8,<9"
RUN useradd --uid 1000 --create-home runner && install -d -o 1000 -g 1000 /workspace
USER 1000:1000
WORKDIR /workspace
