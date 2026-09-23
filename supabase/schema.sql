-- MathExaminer AI - Supabase schema, row-level security and storage policies.
-- Run the whole file once in the Supabase SQL editor. It is idempotent: safe to re-run.
--
-- Security model in one paragraph: every table has RLS on. Clients (the Streamlit app, using
-- the public anon key + the signed-in user's JWT) can only read what they own. Roles are
-- assigned by a trigger, never by the client. Teachers get the 'teacher' role by presenting a
-- code that exists in `teacher_codes`. Flagging and resolving submissions go through
-- SECURITY DEFINER functions so students cannot edit grades and teachers cannot edit
-- other teachers' submissions.

-- ---------------------------------------------------------------- tables

create table if not exists public.users (
    id         uuid primary key references auth.users (id) on delete cascade,
    name       text not null default '',
    role       text not null default 'student' check (role in ('student', 'teacher')),
    created_at timestamptz not null default now()
);

-- Codes a new user can enter at sign-up to become a teacher. Not readable through the API.
create table if not exists public.teacher_codes (
    code       text primary key,
    note       text,
    created_at timestamptz not null default now()
);

create table if not exists public.assignments (
    id                uuid primary key default gen_random_uuid(),
    teacher_id        uuid not null references public.users (id) on delete cascade,
    title             text not null check (char_length(title) between 1 and 200),
    mark_scheme_path  text not null,
    status            text not null default 'active' check (status in ('active', 'closed')),
    created_at        timestamptz not null default now()
);

create table if not exists public.submissions (
    id                    uuid primary key default gen_random_uuid(),
    assignment_id         uuid not null references public.assignments (id) on delete cascade,
    student_id            uuid not null references public.users (id) on delete cascade,
    student_work_paths    text[] not null default '{}',
    ai_feedback           text not null default '',
    confidence_score      integer not null default 0 check (confidence_score between 0 and 100),
    score_awarded         integer not null default 0 check (score_awarded >= 0),
    score_total           integer not null default 0 check (score_total >= 0),
    topic_tag             text not null default 'Other',
    key_takeaway          text not null default '',
    flagged_for_review    boolean not null default false,
    flag_source           text check (flag_source in ('student', 'ai')),
    flag_reason           text,
    flagged_at            timestamptz,
    teacher_score_awarded integer check (teacher_score_awarded >= 0),
    teacher_comment       text,
    resolved_at           timestamptz,
    resolved_by           uuid references public.users (id),
    created_at            timestamptz not null default now()
);

create index if not exists submissions_student_created_idx on public.submissions (student_id, created_at desc);
create index if not exists submissions_assignment_idx      on public.submissions (assignment_id);
create index if not exists submissions_open_flags_idx      on public.submissions (assignment_id)
    where flagged_for_review and resolved_at is null;

-- ---------------------------------------------------------------- helpers

create or replace function public.is_teacher()
returns boolean
language sql stable security definer set search_path = public
as $$
    select exists (select 1 from public.users where id = auth.uid() and role = 'teacher');
$$;

-- Create the profile row when someone signs up. The role comes from a server-side code
-- check, so a client cannot simply claim to be a teacher.
create or replace function public.handle_new_user()
returns trigger
language plpgsql security definer set search_path = public
as $$
declare
    v_role text := 'student';
    v_code text := nullif(trim(new.raw_user_meta_data ->> 'teacher_code'), '');
begin
    if v_code is not null and exists (select 1 from public.teacher_codes where code = v_code) then
        v_role := 'teacher';
    end if;
    insert into public.users (id, name, role)
    values (
        new.id,
        left(coalesce(nullif(trim(new.raw_user_meta_data ->> 'name'), ''), split_part(new.email, '@', 1)), 100),
        v_role
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- Student flags (or disputes) their own submission.
create or replace function public.flag_submission(p_submission_id uuid, p_reason text)
returns void
language plpgsql security definer set search_path = public
as $$
begin
    update public.submissions
       set flagged_for_review = true,
           flag_source        = 'student',
           flag_reason        = left(coalesce(nullif(trim(p_reason), ''), 'Student requested manual review.'), 1000),
           flagged_at         = now()
     where id = p_submission_id and student_id = auth.uid();
    if not found then
        raise exception 'submission not found';
    end if;
end;
$$;

-- The app marks a submission for review when the AI is unsure of the handwriting.
create or replace function public.auto_flag_submission(p_submission_id uuid, p_reason text)
returns void
language plpgsql security definer set search_path = public
as $$
begin
    update public.submissions
       set flagged_for_review = true,
           flag_source        = 'ai',
           flag_reason        = left(coalesce(p_reason, 'Low handwriting confidence.'), 1000),
           flagged_at         = now()
     where id = p_submission_id and student_id = auth.uid() and not flagged_for_review;
end;
$$;

-- Teacher resolves a flagged submission (keep the AI mark or override it).
create or replace function public.resolve_submission(p_submission_id uuid, p_score integer, p_comment text)
returns void
language plpgsql security definer set search_path = public
as $$
declare
    v_total integer;
begin
    select s.score_total into v_total
      from public.submissions s
      join public.assignments a on a.id = s.assignment_id
     where s.id = p_submission_id and a.teacher_id = auth.uid();
    if v_total is null then
        raise exception 'submission not found';
    end if;
    if p_score is not null and (p_score < 0 or p_score > v_total) then
        raise exception 'score must be between 0 and %', v_total;
    end if;
    update public.submissions
       set teacher_score_awarded = p_score,
           teacher_comment       = left(nullif(trim(p_comment), ''), 2000),
           resolved_at           = now(),
           resolved_by           = auth.uid()
     where id = p_submission_id;
end;
$$;

revoke all on function public.flag_submission(uuid, text)           from public, anon;
revoke all on function public.auto_flag_submission(uuid, text)      from public, anon;
revoke all on function public.resolve_submission(uuid, integer, text) from public, anon;
grant execute on function public.flag_submission(uuid, text)           to authenticated;
grant execute on function public.auto_flag_submission(uuid, text)      to authenticated;
grant execute on function public.resolve_submission(uuid, integer, text) to authenticated;

-- ---------------------------------------------------------------- row-level security

alter table public.users         enable row level security;
alter table public.teacher_codes enable row level security;   -- no policies: unreadable via API
alter table public.assignments   enable row level security;
alter table public.submissions   enable row level security;

drop policy if exists users_select on public.users;
create policy users_select on public.users
    for select to authenticated
    using (id = auth.uid() or public.is_teacher());

drop policy if exists assignments_select on public.assignments;
create policy assignments_select on public.assignments
    for select to authenticated
    using (teacher_id = auth.uid() or status = 'active');

drop policy if exists assignments_insert on public.assignments;
create policy assignments_insert on public.assignments
    for insert to authenticated
    with check (teacher_id = auth.uid() and public.is_teacher());

drop policy if exists assignments_update on public.assignments;
create policy assignments_update on public.assignments
    for update to authenticated
    using (teacher_id = auth.uid()) with check (teacher_id = auth.uid());

drop policy if exists assignments_delete on public.assignments;
create policy assignments_delete on public.assignments
    for delete to authenticated
    using (teacher_id = auth.uid());

drop policy if exists submissions_select on public.submissions;
create policy submissions_select on public.submissions
    for select to authenticated
    using (
        student_id = auth.uid()
        or exists (select 1 from public.assignments a
                   where a.id = submissions.assignment_id and a.teacher_id = auth.uid())
    );

drop policy if exists submissions_insert on public.submissions;
create policy submissions_insert on public.submissions
    for insert to authenticated
    with check (
        student_id = auth.uid()
        and not public.is_teacher()
        and exists (select 1 from public.assignments a
                    where a.id = submissions.assignment_id and a.status = 'active')
    );
-- No update/delete policy on submissions: changes go through the functions above.

-- ---------------------------------------------------------------- storage

insert into storage.buckets (id, name, public)
values ('markschemes', 'markschemes', false), ('submissions', 'submissions', false)
on conflict (id) do update set public = false;

-- Mark schemes: files live under "<teacher_id>/<uuid>.<ext>". Any signed-in user may read
-- (the app downloads them server-side to grade); only their owning teacher may write.
drop policy if exists markschemes_read on storage.objects;
create policy markschemes_read on storage.objects
    for select to authenticated using (bucket_id = 'markschemes');

drop policy if exists markschemes_write on storage.objects;
create policy markschemes_write on storage.objects
    for insert to authenticated
    with check (bucket_id = 'markschemes' and public.is_teacher()
                and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists markschemes_delete on storage.objects;
create policy markschemes_delete on storage.objects
    for delete to authenticated
    using (bucket_id = 'markschemes' and (storage.foldername(name))[1] = auth.uid()::text);

-- Student work: files live under "<student_id>/<uuid>.<ext>". Owner reads/writes; teachers read.
drop policy if exists submissions_files_read on storage.objects;
create policy submissions_files_read on storage.objects
    for select to authenticated
    using (bucket_id = 'submissions'
           and ((storage.foldername(name))[1] = auth.uid()::text or public.is_teacher()));

drop policy if exists submissions_files_write on storage.objects;
create policy submissions_files_write on storage.objects
    for insert to authenticated
    with check (bucket_id = 'submissions' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists submissions_files_delete on storage.objects;
create policy submissions_files_delete on storage.objects
    for delete to authenticated
    using (bucket_id = 'submissions' and (storage.foldername(name))[1] = auth.uid()::text);

-- ---------------------------------------------------------------- first teacher
-- Create at least one teacher code, then share it with your teachers (it is entered at sign-up):
--   insert into public.teacher_codes (code, note) values ('choose-a-long-random-string', 'staff room');
