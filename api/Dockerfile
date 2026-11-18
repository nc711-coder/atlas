# Atlas API container for Google Cloud Run. Build from the project folder:
#   gcloud run deploy atlas-api --source api --region us-east4 --allow-unauthenticated
FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./api
ENV PORT=8080
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT}
