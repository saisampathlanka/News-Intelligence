#!/bin/bash
# One-time setup script
set -e

echo "==> Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "==> Installing dependencies..."
pip install -r requirements.txt

echo "==> Downloading spaCy model..."
python -m spacy download en_core_web_sm

echo "==> Copying env file..."
[ ! -f .env ] && cp .env.example .env && echo "  Created .env — add your API keys"

echo "==> Setup complete. Run: source venv/bin/activate && uvicorn backend.main:app --reload"
