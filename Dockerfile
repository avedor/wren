FROM python:3.9-slim-buster

COPY requirements.txt ./
RUN pip install -r requirements.txt

ARG GIT_HASH
ENV GIT_HASH=${GIT_HASH:-dev}

COPY . .

CMD ["python", "wren/core.py"]