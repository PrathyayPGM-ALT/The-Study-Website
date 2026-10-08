-- StudyAI - study loop tables (spaced repetition, quiz scoring, weak topics)
-- Run this in the Supabase SQL editor. Additive only: nothing existing is altered.

-- ---------------------------------------------------------------- flashcards
create table if not exists public.flashcards (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  output_id     uuid references public.saved_outputs(id) on delete set null,
  deck          text not null default 'Default',
  front         text not null,
  back          text not null,
  -- SM-2 scheduling state
  ease          real not null default 2.5,
  interval_days real not null default 0,
  repetitions   int  not null default 0,
  lapses        int  not null default 0,
  due_at        timestamptz not null default now(),
  last_grade    int,
  last_reviewed_at timestamptz,
  suspended     boolean not null default false,
  created_at    timestamptz not null default now()
);

create index if not exists flashcards_due_idx  on public.flashcards (user_id, due_at) where suspended = false;
create index if not exists flashcards_deck_idx on public.flashcards (user_id, deck);

-- ------------------------------------------------------------- card reviews
-- One row per grading. Powers streaks, the review heatmap and lapse analysis.
create table if not exists public.card_reviews (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  card_id        uuid not null references public.flashcards(id) on delete cascade,
  grade          int  not null check (grade between 0 and 5),
  interval_after real,
  ease_after     real,
  reviewed_at    timestamptz not null default now()
);

create index if not exists card_reviews_user_time_idx on public.card_reviews (user_id, reviewed_at desc);

-- ------------------------------------------------------------ quiz attempts
create table if not exists public.quiz_attempts (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  output_id  uuid references public.saved_outputs(id) on delete set null,
  file_ids   jsonb not null default '[]'::jsonb,
  score      int  not null,
  total      int  not null,
  -- [{ "q": "...", "chosen": 2, "answer": 0, "correct": false, "options": [...] }]
  answers    jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists quiz_attempts_user_time_idx on public.quiz_attempts (user_id, created_at desc);

-- ------------------------------------------------------------------ security
alter table public.flashcards    enable row level security;
alter table public.card_reviews  enable row level security;
alter table public.quiz_attempts enable row level security;

do $$
declare
  t text;
begin
  foreach t in array array['flashcards', 'card_reviews', 'quiz_attempts'] loop
    execute format('drop policy if exists %I_owner on public.%I', t, t);
    execute format(
      'create policy %I_owner on public.%I for all
         using (auth.uid() = user_id) with check (auth.uid() = user_id)', t, t);
  end loop;
end $$;
