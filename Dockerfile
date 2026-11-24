FROM python:3.12-slim

WORKDIR /app

# A real font for the LoLdle pictures: Pillow's bundled one has no accents ("Trouvé" prints a box).
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN pip install uv --no-cache-dir

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY bot/ bot/
COPY main.py .

CMD ["uv", "run", "python", "main.py"]
