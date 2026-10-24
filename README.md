# CrewCrate API

FastAPI backend, deployed on Render, with Supabase (Postgres + Auth).

## Run locally

```
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
pytest
```
