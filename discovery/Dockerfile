ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY reconcile.py /app/reconcile.py
USER 65534:65534
ENTRYPOINT ["python3", "-u", "/app/reconcile.py"]
