# CrewCrate API

FastAPI backend for the CrewCrate iOS CRM. It maps the current app's client,
activity, appointment, note, task, financial-document, and media features to
live, workspace-scoped data.

## Architecture

- **Supabase**: Postgres, Auth, and Storage (`crewcrate_media` bucket).
- **Render**: FastAPI web service; `render.yaml` describes the deployment.
- **iOS**: sends a Supabase access token as `Authorization: Bearer ...` and its
  active workspace in `X-Workspace-Id`.

The API verifies the Supabase user and checks workspace membership before every
workspace-scoped request. Keep the secret key on Render only.

## Run locally

1. Copy `.env.example` to `.env` and supply Supabase credentials.
2. Create a private `crewcrate_media` Storage bucket in Supabase.
3. Install with `pip install -e '.[dev]'`.
4. Apply `supabase/migrations/20260908215000_initial_crewcrate_schema.sql` to
   your development project through Supabase's SQL Editor.
5. Run `uvicorn app.main:app --reload`.

Open `/docs` to inspect the generated API contract. Production schema changes
are managed through migrations. Automatic creation is disabled by default.

## Current API surface

- `POST /v1/workspaces`
- CRUD/list routes for clients, activities, appointments, notes, tasks, and
  estimates/invoices with line items
- `POST /v1/media` for uploads to Supabase Storage and `GET /v1/media` for
  metadata, plus a short-lived private download URL
