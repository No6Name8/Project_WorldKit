-- WorldKit schema: paste this whole file into Supabase > SQL Editor and press Run (safe to run twice). Same content as supabase/migrations/20261102000000_projects.sql.

create table if not exists public.projects (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  name        text not null check (char_length(name) between 1 and 120),
  mode        text not null check (mode in ('object', 'map')),
  status      text not null default 'uploaded' check (status in ('uploaded', 'processing', 'done', 'failed')),
  video_path  text,
  result_path text,
  report      jsonb,
  error       text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists projects_user_created_idx on public.projects (user_id, created_at desc);
create index if not exists projects_status_idx on public.projects (status);

create or replace function public.touch_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists projects_touch_updated_at on public.projects;
create trigger projects_touch_updated_at before update on public.projects
  for each row execute function public.touch_updated_at();

alter table public.projects enable row level security;

drop policy if exists "projects_select_own" on public.projects;
drop policy if exists "projects_insert_own" on public.projects;
drop policy if exists "projects_update_own" on public.projects;
drop policy if exists "projects_delete_own" on public.projects;
create policy "projects_select_own" on public.projects for select to authenticated using (user_id = auth.uid());
create policy "projects_insert_own" on public.projects for insert to authenticated with check (user_id = auth.uid());
create policy "projects_update_own" on public.projects for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "projects_delete_own" on public.projects for delete to authenticated using (user_id = auth.uid());

-- Private buckets: videos (phone uploads) and results (STL, GLB, report from the laptop worker).
insert into storage.buckets (id, name, public) values ('videos', 'videos', false), ('results', 'results', false)
on conflict (id) do update set public = false;

-- The first folder of every object path must be the user's own id: <user_id>/<project_id>/<file>.
do $$
declare b text;
begin
  foreach b in array array['videos', 'results'] loop
    execute format('drop policy if exists "%1$s_select_own" on storage.objects', b);
    execute format('drop policy if exists "%1$s_insert_own" on storage.objects', b);
    execute format('drop policy if exists "%1$s_update_own" on storage.objects', b);
    execute format('drop policy if exists "%1$s_delete_own" on storage.objects', b);
    execute format($p$create policy "%1$s_select_own" on storage.objects for select to authenticated using (bucket_id = %1$L and (storage.foldername(name))[1] = auth.uid()::text)$p$, b);
    execute format($p$create policy "%1$s_insert_own" on storage.objects for insert to authenticated with check (bucket_id = %1$L and (storage.foldername(name))[1] = auth.uid()::text)$p$, b);
    execute format($p$create policy "%1$s_update_own" on storage.objects for update to authenticated using (bucket_id = %1$L and (storage.foldername(name))[1] = auth.uid()::text) with check (bucket_id = %1$L and (storage.foldername(name))[1] = auth.uid()::text)$p$, b);
    execute format($p$create policy "%1$s_delete_own" on storage.objects for delete to authenticated using (bucket_id = %1$L and (storage.foldername(name))[1] = auth.uid()::text)$p$, b);
  end loop;
end $$;

-- Realtime: the projects list watches status changes.
do $$
begin
  if not exists (select 1 from pg_publication_tables where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'projects') then
    alter publication supabase_realtime add table public.projects;
  end if;
end $$;

-- 2026-11-04 (supabase/migrations/20261104000000_upload_limits.sql): server-side upload limits
-- Storage refuses anything else with 413 / 415 before it is stored.
update storage.buckets
   set file_size_limit = 52428800,
       allowed_mime_types = array['video/mp4', 'video/quicktime', 'video/x-m4v', 'video/webm', 'video/3gpp']
 where id = 'videos';

update storage.buckets
   set file_size_limit = 52428800,
       allowed_mime_types = array['model/gltf-binary', 'model/stl', 'application/json', 'image/png', 'image/jpeg']
 where id = 'results';
