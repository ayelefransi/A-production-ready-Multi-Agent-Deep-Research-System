-- =============================================================================
-- Multi-Agent Deep Research System: Supabase schema
--
-- Run this once in the Supabase SQL editor (Dashboard > SQL editor > New query).
-- It is idempotent: running it again does not drop data.
--
-- Access model
--   * The FastAPI server uses the service-role key and checks ownership in
--     code on every query. The service role bypasses Row Level Security.
--   * RLS is still enabled with owner-only policies, so the anon and
--     authenticated keys can never read another user's rows if they are used
--     directly against the REST API.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- profiles: one row per auth user (application role and active flag)
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
    id          uuid primary key references auth.users (id) on delete cascade,
    email       text not null,
    full_name   text not null default '',
    role        text not null default 'user' check (role in ('user', 'admin')),
    is_active   boolean not null default true,
    created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- research_runs: one row per research request
-- ---------------------------------------------------------------------------
create table if not exists public.research_runs (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users (id) on delete cascade,
    query         text not null check (char_length(query) between 3 and 2000),
    status        text not null default 'queued' check (status in (
                      'queued', 'running', 'awaiting_approval',
                      'completed', 'failed', 'cancelled')),
    stage         text not null default '',       -- agent currently working
    progress      integer not null default 0 check (progress between 0 and 100),
    round         integer not null default 1,     -- 1 + number of replans
    title         text not null default '',       -- report title, for lists
    source_count  integer not null default 0,
    error         text not null default '',       -- user-facing failure reason
    report        jsonb,                          -- final report document
    created_at    timestamptz not null default now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    updated_at    timestamptz not null default now()
);

-- The dashboard lists a user's runs newest first; recovery scans by status.
create index if not exists research_runs_user_created_idx
    on public.research_runs (user_id, created_at desc);
create index if not exists research_runs_status_idx
    on public.research_runs (status);

-- ---------------------------------------------------------------------------
-- Create a profile automatically when a user signs up
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id, email, full_name)
    values (
        new.id,
        coalesce(new.email, ''),
        coalesce(new.raw_user_meta_data ->> 'full_name', '')
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- Row Level Security: owners only. No insert/update policies exist, so only
-- the server (service role) can write.
-- ---------------------------------------------------------------------------
alter table public.profiles enable row level security;
alter table public.research_runs enable row level security;

drop policy if exists "profiles: read own" on public.profiles;
create policy "profiles: read own" on public.profiles
    for select to authenticated
    using (auth.uid() = id);

drop policy if exists "runs: read own" on public.research_runs;
create policy "runs: read own" on public.research_runs
    for select to authenticated
    using (auth.uid() = user_id);

drop policy if exists "runs: delete own" on public.research_runs;
create policy "runs: delete own" on public.research_runs
    for delete to authenticated
    using (auth.uid() = user_id);
