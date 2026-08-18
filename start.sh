#!/usr/bin/env bash
# EVE Trader launcher (Mac/Linux) - double-click or run `./start.sh`.
# Creates the virtual environment/npm deps on first run, then starts the
# FastAPI backend + React frontend (dev mode) and opens the browser.
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[Setup] Erstelle virtuelle Umgebung..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "[Setup] Pruefe/installiere Python-Abhaengigkeiten..."
pip install -q -r requirements.txt
pip install -q -e .

if [ ! -f ".env" ]; then
    echo "[Setup] Kopiere .env.example zu .env - bitte EVE_SSO_CLIENT_ID eintragen!"
    cp .env.example .env
    ${EDITOR:-nano} .env
fi

if [ ! -f "config.yaml" ]; then
    echo "[Setup] Kopiere config.example.yaml zu config.yaml - bitte structure_id / Charaktere anpassen!"
    cp config.example.yaml config.yaml
    ${EDITOR:-nano} config.yaml
fi

if [ ! -d "frontend/node_modules" ]; then
    echo "[Setup] Installiere Frontend-Abhaengigkeiten (einmalig, kann etwas dauern)..."
    (cd frontend && npm install)
fi

echo ""
echo "[Start] Backend (FastAPI) startet im Hintergrund..."
uvicorn eve_trader.api.main:app --port 8000 &
BACKEND_PID=$!
trap "kill $BACKEND_PID" EXIT

sleep 2

echo "[Start] Frontend startet - Browser oeffnet sich automatisch..."
( sleep 2 && (xdg-open http://localhost:5173 2>/dev/null || open http://localhost:5173 2>/dev/null || true) ) &
(cd frontend && npm run dev)
