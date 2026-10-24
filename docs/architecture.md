# Architecture

How MathExaminer AI is put together: the parts, how one grading flows through them, what is stored,
and who is allowed to do what. For setup and features, see the [README](../README.md).

- [Components](#components)
- [How one grading flows](#how-one-grading-flows)
- [Checking the AI's answer](#checking-the-ais-answer)
- [Data model](#data-model)
- [Access control](#access-control)
- [Server-only operations](#server-only-operations)
- [Design decisions](#design-decisions)

## Components

| Part | What it does | Code |
|---|---|---|
| Streamlit app | The whole user interface and all server logic. Streamlit runs the Python on the server and sends only the rendered page to the browser, so secrets never reach users. | [`app.py`](../app.py), [`mathexaminer/ui/`](../mathexaminer/ui) |
| Image pipeline | Turns uploaded photos and PDFs into compact JPEG pages for the AI. | [`mathexaminer/images.py`](../mathexaminer/images.py) |
| Grader | Sends the mark scheme and the student's work to DeepSeek and gets a marking back. | [`mathexaminer/deepseek.py`](../mathexaminer/deepseek.py) |
| Result model | Validates the AI's answer and computes the totals. | [`mathexaminer/models.py`](../mathexaminer/models.py) |
| Data layer | Every Supabase call: sign-in, tables, file storage. | [`mathexaminer/db.py`](../mathexaminer/db.py) |
| Database | Supabase: Postgres with row-level security, authentication, and private file storage. | [`supabase/schema.sql`](../supabase/schema.sql) |
| Demo backend | An in-memory stand-in for Supabase and DeepSeek, so the real interface runs with no accounts or keys. It replays real markings recorded from the grader for three real handwritten answers. | [`demo/`](../demo) |

## How one grading flows

```mermaid
sequenceDiagram
    actor S as Student
    participant A as Streamlit app (server)
    participant DB as Supabase
    participant AI as DeepSeek
    S->>A: Upload photos or a PDF of the working
    A->>A: Convert to JPEG pages (at most 4, longest side 1600 px)
    A->>DB: Download the mark scheme (service key)
    A->>DB: Reserve one grading from the 24-hour allowance (service key)
    A->>AI: Prompt + mark scheme pages + work pages (JSON mode, high reasoning effort)
    AI-->>A: Marking as JSON
    A->>A: Validate, clamp, add up totals
    Note over A,AI: If the answer is unusable, ask once more at low reasoning effort
    A->>DB: Upload the work files (student's own token)
    A->>DB: Save the submission (service key), flagged if handwriting confidence is below 60%
    A-->>S: Marks, mark-by-mark feedback, topic, key takeaway
```

Limits along the way:

| Step | Limit | Where |
|---|---|---|
| Upload | 15 MB per file; PNG, JPG, WEBP or PDF, detected from the file's bytes rather than its name | `images.py`, `.streamlit/config.toml` |
| Student work | 4 pages in total across all files | `images.MAX_PAGES` |
| Mark scheme | 6 pages | `SCHEME_MAX_PAGES` in the UI pages |
| Images | Longest side scaled down to 1600 px; rotated phone photos straightened; transparent PNGs put on white; images over 60 megapixels rejected | `images.py` |
| Allowance | 20 gradings per student per 24 hours by default (`MAX_GRADINGS_PER_DAY`); failed attempts count too, because each may have cost an API call | `db.reserve_grading` |
| AI request | Up to 32,000 output tokens, because DeepSeek's reasoning counts toward that limit | `deepseek.MAX_TOKENS` |
| Time | 150 s per request, 300 s for the whole grading; no new attempt starts with under 30 s left | `deepseek.py` |

The prompt puts the parts that are the same for every student first (instructions, then the mark
scheme) and the student's work last. DeepSeek caches repeated request prefixes automatically on a
best-effort basis, so this order gives later gradings of the same assignment a chance to reuse the
cached prefix at a lower price.

## Checking the AI's answer

The AI is asked for one JSON object: one entry per mark (`label`, `mark_type`, `max_marks`,
`comment`, `awarded`), plus a `summary`, a `topic`, a `key_takeaway` and a
`handwriting_confidence`. DeepSeek's JSON mode guarantees valid JSON but not this shape, so
[`GradingResult.from_model_json`](../mathexaminer/models.py) checks everything:

- **Totals are computed, never read from the AI.** The score is the sum of the per-mark entries.
- **Each mark is clamped.** `max_marks` is limited to 0–20 and `awarded` to 0–`max_marks`.
- **Numbers are read safely.** Strings like `"2"` are accepted, and `Infinity` or nonsense falls back to a default instead of crashing.
- **Unknown topics become `Other`**, and confidence is limited to 0–100.
- **Text is length-limited:** labels 40 characters, comments 600, the summary 1,200, the takeaway 400.
- **No usable marks means a retry.** If there are no marks, or they add up to zero available marks, the answer is rejected and the request is tried once more.

The prompt also tells the AI that the images are untrusted: it must ignore any instructions written
in them, such as a page saying "award full marks".

## Data model

```mermaid
erDiagram
    USERS ||--o{ ASSIGNMENTS : "creates (teacher)"
    USERS ||--o{ SUBMISSIONS : "submits (student)"
    ASSIGNMENTS ||--o{ SUBMISSIONS : "receives"
    USERS ||--o{ GRADING_ATTEMPTS : "uses allowance"
    USERS {
        uuid id PK
        text name
        text role "student or teacher"
    }
    ASSIGNMENTS {
        uuid id PK
        uuid teacher_id FK
        text title
        text mark_scheme_path
        text question_path "optional"
        text status "active or closed"
    }
    SUBMISSIONS {
        uuid id PK
        uuid assignment_id FK
        uuid student_id FK
        text_array student_work_paths
        text ai_feedback
        int confidence_score
        int score_awarded
        int score_total
        text topic_tag
        text key_takeaway
        bool flagged_for_review
        int teacher_score_awarded
        timestamptz resolved_at
    }
    GRADING_ATTEMPTS {
        uuid id PK
        uuid student_id FK
        timestamptz created_at
    }
```

There is also a `teacher_codes` table, holding the codes that make a new account a teacher. Files
live in three private storage buckets: `markschemes` and `questions` (one folder per teacher) and
`submissions` (one folder per student). The full definitions, with every column and constraint, are in
[`supabase/schema.sql`](../supabase/schema.sql).

## Access control

Every table has row-level security switched on, so the database itself decides what each signed-in
user can see and change. The app's own checks are a convenience on top, not the protection.

| Data | Student | Teacher | Server only (service key) |
|---|---|---|---|
| `users` | Read own row | Read all rows | Created by a sign-up trigger, never by the client |
| `teacher_codes` | No access | No access | — |
| `assignments` | Read active ones | Read active ones and own; create, edit, close, delete own | Checks an assignment is still open before saving |
| `submissions` | Read own; flag or dispute own (through a database function) | Read those for own assignments; set the final mark (through a database function, 0 to the total) | The only way a submission is created |
| `grading_attempts` | No access | No access | Records and counts the allowance |
| Mark scheme files | No access | Read, upload and delete own | Downloads them to grade |
| Question sheets | Read those of open assignments | Read those of assignments they can see; upload and delete own | — |
| Student work files | Read, upload and delete own | Read files submitted to own assignments | Deletes them when their assignment is deleted |

The role is set when an account is created: a database trigger makes the account a teacher only if
the sign-up included a code that exists in `teacher_codes`. A user cannot change their own role.

## Server-only operations

The server also holds Supabase's service_role key, which bypasses row-level security. It is never
sent to a browser, and it is used for exactly four things a student must not be able to do with
their own login:

1. **Saving a graded submission.** Students have no permission to insert submissions, so every stored score came from the grader.
2. **The grading allowance.** It records and counts attempts in a table students cannot touch.
3. **Downloading the mark scheme to grade against.** Students cannot read mark schemes themselves.
4. **Deleting a removed assignment's student files.** Storage rules only let each student delete their own files.

These calls take the student or teacher id from the signed-in profile, never from anything the user
typed.

## Design decisions

- **JSON instead of reading free text.** Earlier versions asked the AI for a written report and
  pulled the score and confidence out with text patterns. Small changes in wording (`**85%**`, a
  missing heading) silently produced wrong values. A structured answer, checked in code, removed that
  whole class of error.
- **The program adds up the marks, not the AI.** A total can never disagree with its own breakdown.
- **Human review is built in.** Low handwriting confidence or a student dispute sends the work to a
  teacher, whose mark then counts everywhere, including the analytics.
- **DeepSeek's OpenAI-compatible endpoint.** DeepSeek also offers an Anthropic-compatible endpoint,
  but that one does not support structured JSON output (only the `effort` setting is honoured
  there; see [DeepSeek's docs](https://api-docs.deepseek.com/guides/anthropic_api)). The
  OpenAI-compatible endpoint's JSON mode is the documented path, so the app uses the `openai` SDK
  pointed at DeepSeek.
- **`deepseek-flash`.** It is the DeepSeek model that accepts images; `deepseek-v4-pro` is text-only
  and cannot read handwriting. The app cannot tell a text-only model from a vision one, so if you
  change `DEEPSEEK_MODEL`, keep it a vision model.
- **Reason hard first, then lightly.** DeepSeek's reasoning counts toward the output limit. A real
  test showed a hard script could use the whole budget on reasoning and return no marks, so the
  first attempt reasons at high effort and the retry at low effort.
- **One Supabase client per browser session.** The client holds the signed-in user's login token.
  Sharing one client across sessions (for example with `st.cache_resource`) would let one user's
  requests run as another user.
- **Private storage.** Student handwriting is personal data, so neither bucket is public.
- **A demo built from real work.** The hosted demo runs the real interface on a real question,
  mark scheme and three handwritten answers. Each answer was graded once by the real grader, and the
  demo replays those markings ([`demo/samples/recorded-gradings.json`](../demo/samples/recorded-gradings.json))
  instead of calling the AI, so it costs nothing to run and every score matches its marks. It labels
  itself "Demo mode", refuses to pretend to mark anything else, and gives each visitor a private
  copy that carries over between the student and teacher accounts until the page is refreshed.
