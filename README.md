# Pasticcio

A federated, open-source recipe social network based on ActivityPub.
Licensed under EUPL-1.2.

---

## What is Pasticcio?

Pasticcio is a recipe sharing platform that speaks ActivityPub, the same
protocol used by Mastodon, Pixelfed, and the rest of the Fediverse. A recipe
published on one Pasticcio instance can be followed, liked, and boosted from
Mastodon or any other compatible platform, with no central server in control.

**Tech stack:** Python 3.12, FastAPI, PostgreSQL, SQLAlchemy (async), Alembic, Celery, Redis, Podman.

---

## Quick start (development)

### Prerequisites

- Podman + podman-compose
- Git

Python, PostgreSQL and Redis all run inside containers.

### First-time setup

```bash
# 1. Clone the repository
git clone https://github.com/TBD/pasticcio.git
cd pasticcio

# 2. Create your local environment file
cp .env.example .env

# 3. Generate a secret key for JWT signing
#    Copy the output and paste it as SECRET_KEY in .env
openssl rand -hex 32

# 4. Build and start all services
podman-compose up -d --build

# 5. Apply database migrations
podman-compose exec web alembic upgrade head

# 6. Verify everything works
podman-compose exec web pytest
```

The API is now available at http://localhost:8000.
Swagger UI at http://localhost:8000/api/docs (only when DEBUG=true).

### Everyday workflow

```bash
podman-compose up -d              # start
podman-compose down               # stop (data preserved)
podman-compose logs -f web        # logs
podman-compose exec web pytest    # tests
podman-compose exec web python    # Python shell
```

### After pulling new code

```bash
git pull
podman-compose up -d --build          # only if requirements.txt changed
podman-compose exec web alembic upgrade head
podman-compose exec web pytest
```

---

## Project structure

```
pasticcio/
|
+-- app/
|   +-- main.py              # FastAPI entry point, router registration
|   +-- config.py            # Settings from environment variables
|   +-- database.py          # SQLAlchemy async engine and session
|   +-- auth.py              # bcrypt hashing, JWT creation/validation
|   |
|   +-- models/
|   |   +-- user.py          # User (local and remote federated)
|   |   +-- recipe.py        # Recipe, RecipeTranslation, RecipeIngredient, RecipePhoto, FoodItem
|   |   +-- follower.py      # Follower relationship (for AP delivery)
|   |
|   +-- routers/
|   |   +-- auth.py          # POST /register, POST /login, GET /me
|   |   +-- recipes.py       # CRUD /api/v1/recipes/
|   |   +-- wellknown.py     # GET /.well-known/webfinger, /nodeinfo
|   |   +-- activitypub.py   # GET /users/{u}, outbox, inbox, followers
|   |
|   +-- ap/
|   |   +-- signatures.py    # RSA key generation, HTTP Signature sign/verify
|   |   +-- builder.py       # Build AP JSON-LD objects (Actor, Article, ...)
|   |
|   +-- utils/
|       +-- serializers.py   # to_schema_org(), to_ap_tags()
|
+-- alembic/versions/
|   +-- 0001_initial_schema.py
|   +-- 0002_add_recipe_categories.py
|   +-- 0003_add_followers.py
|
+-- tests/
|   +-- conftest.py          # fixtures: client, db_session, test_user, auth_headers
|   +-- test_auth.py
|   +-- test_recipes.py
|   +-- test_serializers.py
|   +-- test_wellknown.py
|   +-- test_activitypub.py
|
+-- Dockerfile.dev           # Development image (with --reload)
+-- Dockerfile.prod          # Production image (multi-stage, no build tools)
+-- podman-compose.yml       # web, worker, db, redis, caddy
+-- Caddyfile.dev            # Reverse proxy with auto HTTPS for .localhost
+-- alembic.ini
+-- pytest.ini
+-- requirements.txt
+-- .env.example
```

---

## Environment variables

Copy  to  and edit as needed.

| Variable             | Required | Description                                        |
|----------------------|----------|----------------------------------------------------|
| DATABASE_URL         | yes      | PostgreSQL connection string (asyncpg driver)      |
| SECRET_KEY           | yes      | JWT secret -- use `openssl rand -hex 32`           |
| INSTANCE_DOMAIN      | yes      | Public domain without https://                     |
| REDIS_URL            | no       | Defaults to redis://redis:6379/0                   |
| INSTANCE_NAME        | no       | Display name of this instance                      |
| ENABLE_REGISTRATIONS | no       | true / false, defaults to true                     |
| DEBUG                | no       | true enables Swagger UI and verbose errors         |

---

## Database migrations

```bash
# Apply all pending migrations (run after every git pull)
podman-compose exec web alembic upgrade head

# Check current status
podman-compose exec web alembic current

# Roll back the last migration
podman-compose exec web alembic downgrade -1

# Generate a new migration after changing a model
podman-compose exec web alembic revision --autogenerate -m "describe the change"
```

Convention: ENUM type names in PostgreSQL always have a  suffix
(e.g. , ) to avoid name collisions.

---

## Running tests

```bash
podman-compose exec web pytest                                         # full suite
podman-compose exec web pytest -v                                      # verbose
podman-compose exec web pytest tests/test_activitypub.py -v           # single file
podman-compose exec web pytest tests/test_auth.py::test_register_success  # single test
podman-compose exec web pytest -x                                      # stop at first failure
```

The test database is separate from the development database and is truncated
between each test automatically.

---

## ActivityPub federation

### Finding a Pasticcio user from Mastodon

Search for  in Mastodon's search bar.

### Federation endpoints

| Endpoint                        | Description                           |
|---------------------------------|---------------------------------------|
| GET /.well-known/webfinger      | User discovery by acct: handle        |
| GET /nodeinfo/2.1               | Server capabilities and stats         |
| GET /users/{username}           | Actor profile (JSON-LD)               |
| GET /users/{username}/outbox    | Published recipes as Create{Article}  |
| POST /users/{username}/inbox    | Receive Follow, Undo, Like, Announce  |
| GET /users/{username}/followers | Follower count                        |

All outgoing requests are signed with RSA-SHA256 (HTTP Signatures,
draft-cavage-http-signatures), consistent with Mastodon's implementation.
Keys are generated at user registration.

### Testing federation locally

ActivityPub requires HTTPS. To test with a real Mastodon instance:

- Use ngrok:  gives you a temporary public HTTPS URL
- Or deploy to a VPS with the production setup

### Fediverse bot (planned — v0.6)

Users on any Fediverse platform (Mastodon, Pleroma, Misskey, etc.) will be able
to submit recipes without a Pasticcio account, by sending Direct Messages to the
instance bot account.

Commands: /new, /title, /ingredients, /steps, /tags, /publish, /help

Session state is managed in Redis. Recipes can optionally require admin approval
before publication. See SPEC.md section 5.3 for the full design.

---

## Dietary tags

Pasticcio uses inclusive-only dietary filters -- they work toward
plant-based diets, never as exclusions. Filtering for  shows only
vegan recipes. There is no "contains meat" filter.

Metabolic tags (keto, low_carb, etc.) always trigger a disclaimer in the UI
because they reflect personal health choices, not ethical positions.

---

## Contributing

1. Fork the repository
2. Create a branch: 
3. Make changes and add tests
4. Run  -- all tests must pass
5. Open a pull request

**Conventions:**

- Code and comments in English
- Every new feature needs tests
- ENUM type names in PostgreSQL always have a  suffix

---

## Roadmap

| Version | Status      | Focus                                              |
|---------|-------------|----------------------------------------------------|
| v0.1    | done        | Auth, Recipe CRUD, WebFinger, Actor, Outbox, Inbox |
| v0.2    | in progress | Celery delivery, Like/Announce, federated comments |
| v0.3    | planned     | Recipe translations, photo uploads, CookedThis     |
| v0.4    | planned     | Nutrition module (Open Food Facts integration)     |
| v0.5    | planned     | Frontend (public browser, author dashboard)        |
| v0.6    | planned     | Fediverse bot (DM-based recipe submission)         |
---

## License

European Union Public Licence v1.2 (EUPL-1.2)
