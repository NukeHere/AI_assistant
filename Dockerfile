FROM python:3.13-slim

WORKDIR /app
COPY simple_server.py ./

ENV ASSISTANT_HOST=0.0.0.0
ENV ASSISTANT_PORT=8000
CMD ["python", "simple_server.py"]
