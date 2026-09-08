-- CrewCrate initial application schema.
-- Apply through the Supabase SQL Editor for the first development deployment,
-- then keep future changes as new migrations in this directory.

create extension if not exists pgcrypto;

create table public.workspaces (
  id uuid primary key default gen_random_uuid(),
  name varchar(160) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.workspace_members (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role varchar(30) not null default 'member' check (role in ('owner', 'admin', 'member')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, user_id)
);

create table public.clients (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name varchar(180) not null,
  company varchar(180) not null default '',
  email varchar(320) not null default '',
  phone varchar(60) not null default '',
  address text not null default '',
  notes text not null default '',
  is_favorite boolean not null default false,
  profile_image_path varchar(500),
  last_activity_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.activities (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  title varchar(220) not null,
  detail text not null default '',
  occurred_at timestamptz not null default now(),
  employee varchar(160) not null default 'You',
  status varchar(30) not null default 'new',
  type varchar(50) not null,
  client_id uuid references public.clients(id) on delete cascade,
  related_appointment_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.appointments (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  title varchar(220) not null,
  details text not null default '',
  start_date timestamptz not null,
  end_date timestamptz not null,
  status varchar(30) not null default 'scheduled',
  client_id uuid references public.clients(id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (end_date >= start_date)
);

create table public.crm_notes (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  body text not null,
  client_id uuid references public.clients(id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.crm_tasks (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  title varchar(220) not null,
  due_date timestamptz not null,
  is_complete boolean not null default false,
  client_id uuid references public.clients(id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.financial_documents (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  title varchar(220) not null,
  kind varchar(20) not null check (kind in ('estimate', 'invoice')),
  status varchar(30) not null default 'new',
  issued_at timestamptz not null default now(),
  client_id uuid references public.clients(id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.line_items (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.financial_documents(id) on delete cascade,
  item_description varchar(500) not null,
  quantity double precision not null default 1 check (quantity >= 0),
  unit_price double precision not null default 0 check (unit_price >= 0)
);

create table public.media_attachments (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name varchar(300) not null,
  media_type varchar(80) not null,
  storage_path varchar(500) not null unique,
  client_id uuid references public.clients(id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index workspace_members_user_id_idx on public.workspace_members(user_id);
create index clients_workspace_id_idx on public.clients(workspace_id);
create index activities_workspace_occurred_at_idx on public.activities(workspace_id, occurred_at desc);
create index activities_client_id_idx on public.activities(client_id);
create index appointments_workspace_start_date_idx on public.appointments(workspace_id, start_date);
create index appointments_client_id_idx on public.appointments(client_id);
create index crm_notes_workspace_created_at_idx on public.crm_notes(workspace_id, created_at desc);
create index crm_notes_client_id_idx on public.crm_notes(client_id);
create index crm_tasks_workspace_due_date_idx on public.crm_tasks(workspace_id, due_date);
create index crm_tasks_client_id_idx on public.crm_tasks(client_id);
create index financial_documents_workspace_updated_at_idx on public.financial_documents(workspace_id, updated_at desc);
create index financial_documents_client_id_idx on public.financial_documents(client_id);
create index line_items_document_id_idx on public.line_items(document_id);
create index media_attachments_workspace_created_at_idx on public.media_attachments(workspace_id, created_at desc);
create index media_attachments_client_id_idx on public.media_attachments(client_id);

-- The API uses a direct server-side database connection and checks workspace
-- membership itself. Tables are not exposed automatically through the Data API.
alter table public.workspaces enable row level security;
alter table public.workspace_members enable row level security;
alter table public.clients enable row level security;
alter table public.activities enable row level security;
alter table public.appointments enable row level security;
alter table public.crm_notes enable row level security;
alter table public.crm_tasks enable row level security;
alter table public.financial_documents enable row level security;
alter table public.line_items enable row level security;
alter table public.media_attachments enable row level security;
