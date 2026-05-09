FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --upgrade pip \
 && pip install \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "asyncpg>=0.30" \
    "python-jose[cryptography]>=3.3" \
    "httpx>=0.27" \
    "python-dotenv>=1.0"
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
