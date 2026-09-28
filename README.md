# MathExaminer AI

[![CI](https://github.com/WHYNOT-LUO/mathexaminer-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/WHYNOT-LUO/mathexaminer-ai/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Open the live demo](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://mathexaminer-ai-demo.streamlit.app)

AI-assisted marking of handwritten **CIE A-Level Mathematics** homework, with a human in the loop.

A teacher uploads an official mark scheme once. Students photograph their handwritten solutions and get
examiner-style feedback in seconds: mark-by-mark (M1 / A1 / B1) with reasons, a score, a topic tag and one
"key takeaway" saved to a personal **Error Book**. When the AI is unsure it can read the handwriting, or the
student disagrees, the submission goes to a **teacher review queue** where the teacher can confirm or
override the mark.

> **Try it now: [mathexaminer-ai-demo.streamlit.app](https://mathexaminer-ai-demo.streamlit.app)**
> Log in as `student@demo.school` or `teacher@demo.school` with any password. No sign-up or keys needed.
> The demo runs the real UI against an in-memory backend with a canned marking; each visitor gets their own copy.
>
> Or run the same demo locally:
> `python3 -m pip install -r requirements.txt && python3 -m streamlit run demo/demo_app.py`

## Screenshots

| Student: mark-by-mark feedback | Student: Error Book |
|---|---|
| ![Feedback](docs/screenshots/03-student-feedback.png) | ![Error Book](docs/screenshots/04-error-book.png) |
| **Teacher: review queue** | **Teacher: analytics** |
| ![Review queue](docs/screenshots/06-teacher-review.png) | ![Analytics](docs/screenshots/07-teacher-analytics.png) |

<details><summary>More: login, assignment picker, teacher assignments</summary>

![Login](docs/screenshots/01-login.png)
![Submit](docs/screenshots/02-student-submit.png)
![Assignments](docs/screenshots/05-teacher-assignments.png)

</details>

*(Screenshots are from the built-in demo backend; the marking shown is a canned example.)*

## Features

| | |
|---|---|
| **Structured marking** | DeepSeek returns JSON (one entry per mark: label, type, max, awarded, comment) via DeepSeek's JSON mode, then validated in code. Totals are computed in code, never trusted from the model. |
| **Human in the loop** | Handwriting-confidence score; low confidence auto-flags the work; students can dispute a mark; teachers resolve the queue with an override and comment. |
| **Digital Error Book** | Every submission with its key takeaway, filterable by topic, plus a chart of the student's weakest topics. |
| **Teacher analytics** | AI confidence vs. student mark scatter (spot systematic AI errors), average mark by topic, submissions awaiting review. |
| **Real-world inputs** | Multi-page PDFs and multiple photos, EXIF-rotated phone pictures, transparent PNGs, size and page limits. |
| **Role-based access** | Students and teachers see different portals. Enforced in the database with row-level security, not just in the UI; scores are written only by the server, never by the student's own token. |
| **Cost control** | Per-student 24-hour grading allowance that counts every attempt and is re-checked when grading starts, one automatic retry on transient API errors, and a time limit on each grading. |

## How it works

```mermaid
flowchart LR
    T[Teacher] -->|uploads mark scheme| SB[(Supabase<br/>Auth, Postgres, Storage)]
    S[Student] -->|photos / PDF of working| APP[Streamlit app]
    APP -->|mark scheme + work as JPEG pages| G[DeepSeek API]
    G -->|JSON, validated in code| APP
    APP -->|score, feedback, topic, takeaway| SB
    APP -->|feedback + Error Book| S
    SB -->|flagged / low confidence| Q[Review queue]
    Q -->|confirm or override| T
```

The original design sketch is in [`docs/workflow.svg`](docs/workflow.svg).

## Setup

### 1. Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Open **SQL editor**, paste the whole of [`supabase/schema.sql`](supabase/schema.sql) and run it (idempotent).
   It creates the tables, row-level-security policies, private storage buckets and the sign-up trigger.
3. Create a teacher access code (teachers enter it when they sign up; students leave it blank):
   ```sql
   insert into public.teacher_codes (code, note) values ('choose-a-long-random-string', 'staff room');
   ```
4. *(Optional, for demos)* **Authentication → Providers → Email**: turn off "Confirm email" so new
   accounts can log in immediately.
5. From **Project Settings → API** copy the **Project URL**, the **anon / publishable** key and the
   **service_role** key. The service_role key bypasses row-level security, so it must stay on the server:
   put it only in `.streamlit/secrets.toml` (git-ignored) or your host's secrets settings. Streamlit runs the
   Python on the server and never sends secrets to the browser. The app uses it only for writes a student
   must not be able to forge (see [Security model](#security-model-and-known-limitations)).

### 2. DeepSeek

Create an API key at [platform.deepseek.com](https://platform.deepseek.com/) and add some credit. The default
model, `deepseek-flash`, is the only current DeepSeek model with image input - `deepseek-v4-pro` is text-only
and cannot grade handwriting. DeepSeek automatically caches repeated prompt prefixes (like the mark scheme),
so after the first student on an assignment, later gradings are billed at a much cheaper cache-hit rate while
the cache is warm - no code needed on your side. If the API is not reachable from your network, deploy the
small relay in [`docs/cloudflare-worker.js`](docs/cloudflare-worker.js) and set `DEEPSEEK_BASE_URL`.

### 3. Run

```bash
git clone https://github.com/WHYNOT-LUO/mathexaminer-ai.git && cd mathexaminer-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill in your values
streamlit run app.py
```

### Configuration

Set in `.streamlit/secrets.toml` (git-ignored) or as environment variables.

| Key | Required | Default | Purpose |
|---|---|---|---|
| `SUPABASE_URL` | yes | | Project URL |
| `SUPABASE_KEY` | yes | | anon / publishable key |
| `SUPABASE_SERVICE_KEY` | yes | | service_role key, server-side only (saves grades, the allowance, mark-scheme downloads) |
| `DEEPSEEK_API_KEY` | yes | | DeepSeek platform API key |
| `DEEPSEEK_MODEL` | no | `deepseek-flash` | Must be a vision-capable DeepSeek model |
| `DEEPSEEK_BASE_URL` | no | DeepSeek's endpoint | Reverse proxy, see the Cloudflare worker |
| `MAX_GRADINGS_PER_DAY` | no | `20` | Per-student cap to protect your quota |

Deploying to Streamlit Community Cloud: paste the same keys into the app's **Secrets** settings.

## Project layout

```
app.py                     Entrypoint: config check, per-session Supabase clients, role routing
mathexaminer/
  config.py                Settings from secrets / environment, validated
  images.py                Photos and PDFs -> JPEG pages (EXIF, alpha, size and page limits)
  deepseek.py              DeepSeek (OpenAI-compatible) client: prompt, JSON mode, safe error messages
  models.py                GradingResult: validation, score computation, markdown rendering
  db.py                    Supabase auth, tables, storage, RPCs; user client vs server-only service client
  ui/                      Streamlit pages: auth, student, teacher, analytics, shared components
supabase/schema.sql        Tables, RLS policies, storage policies, SQL functions
demo/                      In-memory backend so the UI runs with no accounts or keys
tests/                     pytest suite (unit tests + Streamlit AppTest UI tests)
.github/workflows/          CI (ruff + pytest) and a 6-hourly visit that keeps the hosted demo awake
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest          # unit tests and UI tests against the demo backend
ruff check .
```

The DeepSeek client is tested with a fake SDK client and real SDK exception types (auth, rate limit,
malformed output; error messages must never contain the API key). The UI tests drive the real app with Streamlit's `AppTest`, including
login, grade dispute, teacher resolution, deleting an assignment and HTML-injection attempts. `db.py` is
tested against an in-memory stand-in for the Supabase client (the grading allowance, server-side saves with
AI flags, closed assignments, file cleanup). A contract test keeps the demo backend's functions identical to
`mathexaminer/db.py` and covers every public one.

## Design decisions

- **JSON output instead of regex.** The first prototype asked the model for a free-text report and parsed
  the score and confidence out with regular expressions. Any formatting drift (`**85%**`, a missing heading)
  silently produced wrong values. Now the model is required to return a single JSON object (DeepSeek's JSON
  mode guarantees valid JSON syntax, not the specific shape), which is then validated against the expected
  shape in code with one automatic retry on a bad response; per-mark `awarded` is clamped to `max_marks` and
  the totals are summed in code, so a score can never contradict its own breakdown.
- **DeepSeek's OpenAI-compatible endpoint, not its Anthropic-compatible one.** DeepSeek offers both, but its
  Anthropic-compatible endpoint does not support JSON-schema structured outputs (only the `effort` field is
  honored there) - see [their docs](https://api-docs.deepseek.com/guides/anthropic_api). The OpenAI-compatible
  endpoint's `response_format: json_object` is the documented, reliable path, so `mathexaminer/deepseek.py`
  uses the `openai` SDK pointed at DeepSeek instead.
- **`deepseek-flash`, not `deepseek-v4-pro`.** Only `deepseek-flash` accepts image input; `deepseek-v4-pro`
  is text-only and cannot read handwriting. `config.py` rejects an obviously invalid model id, but it cannot
  tell a text-only model apart from a vision one - if you change `DEEPSEEK_MODEL`, keep it a vision model.
- **One Supabase client per browser session.** The client stores the signed-in user's JWT. Sharing one
  client across sessions (e.g. with `st.cache_resource`) would let one user's requests run with another's
  identity.
- **Roles are decided by the database.** A trigger creates the profile and grants `teacher` only if the
  sign-up carried a valid code from `teacher_codes`. Clients have no permission to write roles or grades
  directly; flagging and resolving go through `SECURITY DEFINER` functions.
- **Trusted writes use a server-side key.** Everything a user does runs with their own JWT under row-level
  security, except four server-only steps that use the service_role key: saving a graded submission (students
  have no insert policy, so a score always comes from the grader), recording grading attempts for the
  allowance, downloading the mark scheme to grade against, and deleting a removed assignment's student files.
  Those calls take the student or teacher id from the signed-in profile, never from user input.
- **Private buckets.** Student handwriting is personal data, so nothing is publicly readable.
- **Untrusted images.** The prompt tells the model to ignore instructions written inside the images
  (e.g. a page saying "award full marks").

## Security model and known limitations

- **Scores come only from the grader.** Students have no insert or update rights on submissions; the server
  writes each result with the service_role key, and teachers can override any mark from the review queue.
- **Students cannot download mark schemes**, but the feedback on a submitted answer explains what the scheme
  expected, so do not use this for high-stakes unseen exams.
- **AI marking can be wrong.** Confidence is the model's own estimate of how well it *read* the
  handwriting, not a guarantee of correctness. That is why the review queue exists.
- **No classes or deadlines yet.** Every student sees every open assignment.
- **Sessions are per browser tab.** A page refresh logs you out.
- Deleting an assignment removes its submissions and the students' stored files.

## Roadmap

Classes and enrolment, deadlines, email notifications for flagged work, a class-summary digest of common
errors, and per-question marks entered by the teacher (removing the mark-scheme OCR step).

## License

[MIT](LICENSE)
