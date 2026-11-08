FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hblive ./hblive
COPY web ./web
RUN mkdir -p data && useradd -r hblive && chown hblive data
USER hblive
EXPOSE 8000
CMD ["python", "-m", "hblive.server", "--port", "8000"]
