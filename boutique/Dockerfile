FROM python:3.12-slim

WORKDIR /app
COPY boutique/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/boutique ./src/boutique

ENV PYTHONPATH=/app/src
EXPOSE 8000

CMD ["uvicorn", "boutique.main:app", "--host", "0.0.0.0", "--port", "8000"]
