# Synthetic AITU Registration Service image (educational use only).
FROM python:3.12-slim

# pg8000 is a pure-Python PostgreSQL driver (no compiler / libpq needed).
RUN pip install --no-cache-dir pg8000==1.31.2 \
    && useradd --uid 10001 --create-home appuser

WORKDIR /app
COPY registration_service.py .

USER 10001
EXPOSE 8080
ENV PYTHONUNBUFFERED=1 PORT=8080
ENTRYPOINT ["python", "/app/registration_service.py"]
