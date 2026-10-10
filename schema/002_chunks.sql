-- StudyAI - document chunking + retrieval
-- Run in the Supabase SQL editor, after 001_study_loop.sql.
--
-- Why: build_notes_context sent whole files to the model. Groq's free tier
-- allows 8000 tokens/minute, so one real lecture PDF exceeded the budget and
-- every request failed with HTTP 413. Chunks let us send only the passages
-- that matter to the question, and let answers cite where they came from.

create extension if not exists pg_trgm;

create table if not exists public.document_chunks (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  file_id     uuid not null references public.files(id) on delete cascade,
  filename    text not null,
  chunk_index int  not null,
  content     text not null,
  char_start  int,
  char_end    int,
  token_est   int  not null default 0,
  created_at  timestamptz not null default now(),
  -- Generated so it can never drift out of sync with content.
  tsv tsvector generated always as (to_tsvector('english', content)) stored
);

create index if not exists document_chunks_tsv_idx   on public.document_chunks using gin (tsv);
create index if not exists document_chunks_file_idx  on public.document_chunks (user_id, file_id, chunk_index);
create index if not exists document_chunks_trgm_idx  on public.document_chunks using gin (content gin_trgm_ops);

alter table public.document_chunks enable row level security;
drop policy if exists document_chunks_owner on public.document_chunks;
create policy document_chunks_owner on public.document_chunks for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Ranked retrieval. Falls back to trigram similarity when the query has no
-- usable lexemes (e.g. a very short or all-stopword question), so a search
-- never comes back empty just because of stemming.
create or replace function public.match_chunks(
  p_user_id  uuid,
  p_file_ids uuid[],
  p_query    text,
  p_limit    int default 8
)
returns table (
  id uuid, file_id uuid, filename text, chunk_index int,
  content text, token_est int, rank real
)
language sql
stable
security invoker
set search_path = public, pg_catalog
as $$
  with q as (select websearch_to_tsquery('english', p_query) as tsq)
  select c.id, c.file_id, c.filename, c.chunk_index, c.content, c.token_est,
         case
           when (select tsq from q) is null or (select tsq from q)::text = ''
             then similarity(c.content, p_query)
           else ts_rank(c.tsv, (select tsq from q))
         end as rank
    from public.document_chunks c
   where c.user_id = p_user_id
     and (p_file_ids is null or array_length(p_file_ids, 1) is null
          or c.file_id = any(p_file_ids))
     and (
       (select tsq from q) is null
       or (select tsq from q)::text = ''
       or c.tsv @@ (select tsq from q)
       or similarity(c.content, p_query) > 0.1
     )
   order by rank desc, c.chunk_index
   limit greatest(1, least(p_limit, 50));
$$;

grant execute on function public.match_chunks(uuid, uuid[], text, int) to authenticated, service_role;
