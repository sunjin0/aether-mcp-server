FROM docker:27-cli
# The control plane needs only Docker CLI plus Python's standard library; do
# not install a Docker daemon or a general build toolchain into this service.
RUN apk add --no-cache python3
WORKDIR /runner
COPY sandbox-runner/runner.py ./runner.py
CMD ["python", "runner.py"]
