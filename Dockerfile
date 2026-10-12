FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY scripts/ ./scripts/
RUN python scripts/install_pot_provider.py
COPY simple_server.py ./
COPY app/ ./app/

ENV ASSISTANT_HOST=0.0.0.0
ENV ASSISTANT_PORT=8000
CMD ["python", "simple_server.py"]
