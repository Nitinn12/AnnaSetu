# AnnaSetu

**Where surplus meets the hungry.**

AnnaSetu connects food donors (college canteens, hostel messes) with nearby NGOs and community kitchens to rescue surplus food. Built for ACE Builders Hackathon 2026, Problem Statement 2: Rescue the Surplus.

## Synthetic data notice

- All data in this project is fictional and generated for demonstration.
- AnnaSetu is not a food safety certification. Do not rely on it for food safety decisions.
- No real addresses or personal details are stored or shown.

## Status

Phase 1 (project setup and UI foundation), the donor / NGO donation workflow, the volunteer pickup / delivery workflow and the impact dashboard are complete.

### Donation workflow

| Who | What | Route |
|-----|------|-------|
| Donor | List own donations, post food (with optional photo), edit while still *posted*, cancel while *posted* or *claimed* | `/donate`, `/donate/new`, `/donate/<id>/edit`, `/donate/<id>/cancel` |
| Donor / NGO | Organisation profile (zone, capacity and accepted food types for NGOs) | `/profile/organization` |
| NGO | Browse open donations that fit their capacity, food types, distance and timing; search and filter | `/find-food` |
| NGO | Claim a donation, see claims and pickup address, release a claim | `/find-food/<id>/claim`, `/claims`, `/claims/<id>/release` |
| Donor, claiming NGO, volunteer, admin | Donation detail and status history | `/donations/<id>` |
| Claiming NGO, admin | Assign a volunteer to a claimed donation; remove them again before pickup | `/donations/<id>/assign`, `/donations/<id>/unassign` |
| Admin | See claimed and in-progress donations and manage their volunteers | `/admin/assignments` |
| Volunteer | See assigned tasks with pickup details, mark them collected, then delivered | `/volunteer`, `/volunteer/tasks/<id>/collect`, `/volunteer/tasks/<id>/deliver` |

Rules enforced on the server:

- Every form is validated (`app/donations/validation.py`); invalid input is re-shown with messages and nothing is saved.
- Routes are role-protected (`role_required`). Donors only touch their own donations; NGOs only touch their own claims.
- A donation can have only one active claim. This is enforced twice: the claim runs inside a row-locked transaction, and a partial unique index (`uq_claim_one_active`) makes the database reject a second active claim even under a race.
- Only the NGO that claimed a donation (or an admin) can assign or remove its volunteer, and only volunteer accounts that are active can be assigned. A donation has at most one active assignment (database-enforced, like claims).
- A volunteer can only update tasks assigned to them, in order: assigned, collected, delivered. Each step is time-stamped on the assignment (`assigned_at`, `collected_at`, `delivered_at`, `removed_at`) and in the status history, which also records who acted and in what role.
- While a volunteer is assigned the claim cannot be released and the donor cannot cancel; remove the volunteer first (possible only before pickup).
- All status changes go through `app/tracking.change_status`, which checks the allowed transitions and writes the status history.

### Impact dashboard

`/impact` shows real figures computed from the database (no hard-coded numbers). What each role sees:

| Role | Scope | Figures |
|------|-------|---------|
| Admin | Whole platform | Total, pending, claimed, delivered and expired donations; food rescued (kg) and estimated meals; active volunteers and volunteer deliveries; NGOs participating and NGOs that received food; donors posting; status and category breakdowns; top NGOs |
| Donor | Only their own donations | Same donation counts and food rescued; NGOs helped and which NGOs received their food |
| NGO / receiver | Only their organisation's claims | Claims made, in progress, delivered, expired and released; food received; donors who supplied them |
| Volunteer | Only their own tasks | Tasks assigned, in progress and delivered; food delivered |

Notes:

- **Date filter:** `/impact?start=YYYY-MM-DD&end=YYYY-MM-DD` (both optional, inclusive, India Standard Time). The page has a form and quick links for the last 7 and 30 days. Invalid or reversed dates show a message and fall back to all time. Donation figures filter on the day the food was posted, claim figures on the day it was claimed, volunteer figures on the day of assignment.
- **Definitions:** *Pending* is posted and waiting for an NGO. *Claimed* is claimed, assigned or collected but not yet delivered. *Food rescued* counts delivered donations only; portions are converted to kg at 0.5 kg each and meals are estimated at 0.4 kg each (both shown on the page as assumptions).
- **Expired** counts donations the system has already marked expired. Run `flask expire-overdue` regularly (for example from a scheduler) so overdue donations are marked.
- The dashboard only reads data. No existing tables, workflows or status rules were changed and no migration is needed.

## Windows setup

Run these commands in Command Prompt from the project folder:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
set FLASK_APP=run.py
flask init-db
flask seed-demo
python run.py
```

Then open http://127.0.0.1:5000 in your browser.

## Demo accounts

Password for all accounts: `Demo@12345`

| Email | Role |
|-------|------|
| admin@example.com | Admin |
| donor@example.com | Donor (canteen / mess) |
| ngo@example.com | NGO / receiver |
| volunteer@example.com | Volunteer |

## Database

`flask init-db` creates the tables for a quick local demo. To manage the schema with migrations instead, run `flask db upgrade`.

## Running the tests

```bat
pip install -r requirements-dev.txt
python -m pytest tests
```

Each test uses its own temporary SQLite database, so your `instance/annasetu.db` is never touched.

## Project structure

```text
annasetu/
├── app/
│   ├── __init__.py          # app factory
│   ├── auth/                # login, register, logout
│   ├── main/                # landing, about, food safety
│   ├── donations/           # donor features: post, edit, cancel, organisation profile
│   ├── receivers/           # NGO features: find food, claim, release
│   ├── services/            # claim / release / cancel business logic
│   ├── volunteers/          # volunteer tasks, NGO/admin assignment
│   ├── impact/              # impact dashboard: role-scoped queries (service.py) and route
│   ├── matching/            # matching engine (Phase 2)
│   ├── tracking/            # tracking timeline (Phase 3)
│   ├── ai/                  # Gemini assistant (Phase 4)
│   ├── templates/           # Jinja2 templates (Bootstrap 5)
│   └── static/
│       ├── css/style.css
│       └── js/main.js
├── run.py                   # entry point
├── requirements.txt
├── requirements-dev.txt     # adds pytest
├── tests/                   # pytest suite
├── .env.example
└── .gitignore
```

## Completed in Phase 1

- Flask app factory, configuration, and SQLAlchemy models
- CSRF protection and `flask init-db` / `flask seed-demo` CLI commands
- Auth blueprint (login, register, logout)
- Base, landing, about, food safety, login, register, placeholder and error templates
- Responsive static CSS and vanilla JS (password toggle, count-up stats, alert auto-dismiss)

## Remaining

- **Phase 2:** quick donate form (under 60 seconds), matching engine, claims
- **Phase 3:** tracking timeline and volunteer task workflow
- **Phase 4:** Gemini assistant and explanations, admin console

## Known limitations

- Impact figures reflect stored donation statuses; overdue donations only show as expired after `flask expire-overdue` has run.
- Logout is a GET link for hackathon simplicity.
- CSRF token is required on forms; keep the session cookie enabled.
- SQLite is for local demo use only and is not suitable for production.
- Bootstrap and fonts are loaded from a CDN, so an internet connection is needed for full styling.
