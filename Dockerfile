FROM python:3.12-slim

# libmagic for python-magic; WeasyPrint runtime libs for invoice/statement PDF generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    curl \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==1.8.5

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --without dev

COPY . .

# scripts must be executable
RUN chmod +x scripts/*

EXPOSE 8000
