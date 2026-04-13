FROM python:3.12-slim

WORKDIR /app

RUN pip install uv --no-cache-dir

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY bot/ bot/
COPY main.py .

CMD ["uv", "run", "python", "main.py"]
