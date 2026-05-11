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
git clone https://github.com/oloturia/pasticcio.git
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
|   |   +-- recipe.py        # Recipe, RecipeTranslation, RecipeIngredient,
|   |   |                    # RecipePhoto, FoodItem
|   |   +-- follower.py      # Follower relationship (for AP delivery)
|   |   +-- reaction.py      # Like and Announce from Fediverse
|   |   +-- cooked_this.py   # Federated comments (CookedThis)
|   |   +-- known_instance.py # Known Fediverse instances
|   |   +-- moderation.py    # InstanceRule, UserBlock
|   |   +-- bookmark.py      # Recipe bookmarks
|   |
|   +-- routers/
|   |   +-- auth.py          # POST /register, POST /login, GET /me
|   |   +-- recipes.py       # CRUD /api/v1/recipes/, fork
|   |   +-- comments.py      # GET/POST /api/v1/recipes/{id}/comments
|   |   +-- photos.py        # GET/POST/DELETE /api/v1/recipes/{id}/photos
|   |   +-- users.py         # GET /api/v1/users/{username}
|   |   +-- search.py        # GET /api/v1/search/, /api/v1/search/federated
|   |   +-- lookup.py        # GET /api/v1/lookup/
|   |   +-- moderation.py    # block, mute, bookmark, admin endpoints
|   |   +-- wellknown.py     # GET /.well-known/webfinger, /nodeinfo
|   |   +-- activitypub.py   # GET /users/{u}, outbox, inbox, shared inbox
|   |
|   +-- ap/
|   |   +-- signatures.py    # RSA key generation, HTTP Signature sign/verify
|   |   +-- builder.py       # Build AP JSON-LD objects (Actor, Article, ...)
|   |   +-- federation.py    # is_federation_allowed() — blacklist/whitelist policy
|   |   +-- instances.py     # fetch_nodeinfo(), get_pasticcio_instances()
|   |   +-- ratelimit.py     # Rate limiting for AP inbox (Redis-backed)
|   |
|   +-- tasks/
|   |   +-- delivery.py      # Celery: deliver AP activities to remote inboxes
|   |   +-- instances.py     # Celery: async NodeInfo check for known instances
|   |
|   +-- templates/
|   |   +-- base.html        # Base layout (craft/paper palette)
|   |   +-- user_profile.html # Public user profile with recipe list
|   |   +-- recipe_detail.html # Recipe page with ingredients and steps
|   |
|   +-- utils/
|       +-- serializers.py   # to_schema_org(), to_ap_tags()
|
+-- alembic/versions/
|   +-- 0001_initial_schema.py      # users, recipes, translations, ingredients, photos
|   +-- 0002_add_recipe_categories.py
|   +-- 0003_add_followers.py
|   +-- 0004_add_reactions.py       # Like, Announce
|   +-- 0005_add_cooked_this.py     # Federated comments
|   +-- 0006_add_forked_from.py     # Recipe fork support
|   +-- 0007_add_fulltext_indexes.py # GIN indexes for search
|   +-- 0008_add_known_instances.py
|   +-- 0009_add_moderation.py      # user_blocks, instance_rules, bookmarks
|
+-- tests/
|   +-- conftest.py             # fixtures: client, db_session, test_user, auth_headers
|   +-- test_auth.py
|   +-- test_recipes.py
|   +-- test_activitypub.py
|   +-- test_wellknown.py
|   +-- test_comments.py
|   +-- test_photos.py
|   +-- test_users.py
|   +-- test_delivery.py
|   +-- test_ratelimit.py
|   +-- test_middleware.py
|   +-- test_fork.py
|   +-- test_inbox_article.py
|   +-- test_search.py
|   +-- test_lookup.py
|   +-- test_instances.py
|   +-- test_moderation.py
|   +-- test_federation.py      # blacklist/whitelist policy, unit + integration
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

Copy `.env.example` to `.env` and edit as needed.

| Variable              | Required | Description                                           |
|-----------------------|----------|-------------------------------------------------------|
| DATABASE_URL          | yes      | PostgreSQL connection string (asyncpg driver)         |
| SECRET_KEY            | yes      | JWT secret — use `openssl rand -hex 32`               |
| INSTANCE_DOMAIN       | yes      | Public domain without https://                        |
| REDIS_URL             | no       | Defaults to redis://redis:6379/0                      |
| INSTANCE_NAME         | no       | Display name of this instance                         |
| INSTANCE_DESCRIPTION  | no       | Short description shown in NodeInfo                   |
| ENABLE_REGISTRATIONS  | no       | true / false, defaults to true                        |
| ENABLE_NUTRITION      | no       | true / false, enables FoodItem module                 |
| FEDERATION_MODE       | no       | `blacklist` (default) or `whitelist`                  |
| COMMENTS_MODERATION   | no       | `off` (default) or `on` — holds federated comments   |
| STORAGE_BACKEND       | no       | `local` (default) or `s3`                            |
| DEBUG                 | no       | true enables Swagger UI and verbose errors            |

### Federation mode

`FEDERATION_MODE=blacklist` (default): all instances are allowed except those
explicitly blocked via the admin API (`rule_type=block`).

`FEDERATION_MODE=whitelist`: no instances are allowed except those explicitly
approved via the admin API (`rule_type=allow`). An empty whitelist means the
instance is completely closed to federation.

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

Convention: ENUM type names in PostgreSQL always have a `type` suffix
(e.g. `recipestatustype`, `ingredientunittype`) to avoid name collisions.

---

## Running tests

```bash
podman-compose exec web pytest                                          # full suite
podman-compose exec web pytest -v                                       # verbose
podman-compose exec web pytest tests/test_activitypub.py -v            # single file
podman-compose exec web pytest tests/test_auth.py::test_register_success  # single test
podman-compose exec web pytest -x                                       # stop at first failure
```

The test database is separate from the development database and is truncated
between each test automatically.

---

## REST API

### Auth — `/api/v1/auth/`

| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| POST   | /register           | Create a new account (RSA keypair generated automatically) |
| POST   | /login              | Get a JWT token (OAuth2 password flow) |
| GET    | /me                 | Current user profile               |

### Recipes — `/api/v1/recipes/`

| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| POST   | /                   | Create a recipe (draft or published) |
| GET    | /                   | List published recipes (dietary filters, pagination) |
| GET    | /{id}               | Get a single recipe                |
| PUT    | /{id}               | Update a recipe (author only)      |
| DELETE | /{id}               | Soft-delete a recipe (author only) |
| POST   | /{id}/fork          | Fork a remote recipe via AP ID     |

### Comments — `/api/v1/recipes/{id}/comments`

| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| GET    | /                   | List comments (threaded, up to 3 levels) |
| POST   | /                   | Post a comment                     |
| DELETE | /{comment_id}       | Delete a comment (author only)     |

### Photos — `/api/v1/recipes/{id}/photos`

| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| GET    | /                   | List photos                        |
| POST   | /                   | Upload a photo (JPEG/PNG/WebP/GIF, max 10MB) |
| DELETE | /{photo_id}         | Delete a photo (author only)       |

### Users — `/api/v1/users/`

| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| GET    | /{username}         | Public profile with last 10 recipes |

### Search — `/api/v1/search/`

| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| GET    | /                   | Full-text search: title, description, ingredients, hashtags |
| GET    | /federated          | Search across all known Pasticcio instances |

### Lookup — `/api/v1/lookup/`

| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| GET    | /?handle=           | Remote user profile via WebFinger  |
| GET    | /?url=              | Remote recipe preview for forking  |

### Moderation — `/api/v1/`

| Method | Endpoint                    | Description                        |
|--------|-----------------------------|------------------------------------|
| POST   | /users/{ap_id}/block        | Block a remote user                |
| DELETE | /users/{ap_id}/block        | Unblock a remote user              |
| GET    | /blocks                     | List blocked users                 |
| POST   | /users/{ap_id}/mute         | Mute a remote user                 |
| DELETE | /users/{ap_id}/mute         | Unmute a remote user               |
| GET    | /mutes                      | List muted users                   |
| POST   | /bookmarks                  | Bookmark a recipe                  |
| DELETE | /bookmarks/{id}             | Remove a bookmark                  |
| GET    | /bookmarks                  | List bookmarks                     |

### Admin — `/api/v1/admin/` (requires `is_admin=true`)

| Method | Endpoint                    | Description                        |
|--------|-----------------------------|------------------------------------|
| GET    | /instances                  | List known Fediverse instances      |
| POST   | /instances                  | Add a federation rule (block/allow) |
| DELETE | /instances/{domain}         | Remove a federation rule            |
| POST   | /users/{id}/ban             | Ban a local user                    |

---

## ActivityPub federation

### Finding a Pasticcio user from Mastodon

Search for `@username@your-instance-domain` in Mastodon's search bar.

### Federation endpoints

| Endpoint                        | Description                                    |
|---------------------------------|------------------------------------------------|
| GET /.well-known/webfinger      | User discovery by acct: handle                 |
| GET /.well-known/nodeinfo       | NodeInfo discovery                             |
| GET /nodeinfo/2.1               | Server capabilities and software info          |
| GET /users/{username}           | Actor profile (JSON-LD) or HTML (browsers)     |
| GET /users/{username}/outbox    | Published recipes as Create{Article}           |
| GET /users/{username}/followers | Follower count (no enumeration for privacy)    |
| POST /users/{username}/inbox    | Personal inbox                                 |
| POST /inbox                     | Shared inbox (routes to personal inboxes)      |

### Incoming activities handled

| Activity          | Effect                                         |
|-------------------|------------------------------------------------|
| Follow            | Stores follower, sends Accept                  |
| Undo{Follow}      | Removes follower                               |
| Like              | Stores reaction                                |
| Undo{Like}        | Removes reaction                               |
| Announce          | Stores reaction                                |
| Undo{Announce}    | Removes reaction                               |
| Create{Note}      | Creates federated comment (CookedThis)         |
| Update{Note}      | Updates comment content (author only)          |
| Delete{Note}      | Removes comment (author only)                  |
| Delete{Article}   | Soft-deletes local recipe (author only)        |

### Federation policy

Controlled by `FEDERATION_MODE` in `.env`:

- **blacklist** (default): all instances allowed, specific domains blocked via admin API
- **whitelist**: all instances blocked, specific domains allowed via admin API
- Empty whitelist = fully closed instance (no incoming federation at all)

All outgoing requests are signed with RSA-SHA256 (HTTP Signatures,
draft-cavage-http-signatures), consistent with Mastodon's implementation.
Keys are generated automatically at user registration.

### Rate limiting

The AP inbox is rate-limited per IP and per domain (Redis-backed, configurable
via `.env`). If Redis is unavailable, requests pass through (fail open).

### Testing federation locally

ActivityPub requires HTTPS. To test with a real Mastodon instance:

- Use [ngrok](https://ngrok.com/): gives you a temporary public HTTPS URL
- Or deploy to a VPS with the production setup

---

## Dietary tags

Pasticcio uses inclusive-only dietary filters — they work toward
plant-based diets, never as exclusions. Filtering for `vegan` shows only
vegan recipes. There is no "hide vegan" option.

Metabolic tags (`low_carb`, `high_protein`, etc.) always trigger a disclaimer
in the UI because they reflect personal health choices, not ethical positions.

---

## Contributing

1. Fork the repository
2. Create a branch: `git checkout -b feature/my-feature`
3. Make changes and add tests
4. Run `pytest` — all tests must pass
5. Open a pull request

**Conventions:**

- Code and comments in English
- Every new feature needs tests
- ENUM type names in PostgreSQL always have a `type` suffix

---

## Roadmap

| Version | Status       | Focus                                                   |
|---------|--------------|---------------------------------------------------------|
| v0.1    | ✅ done      | Auth, Recipe CRUD, WebFinger, Actor, Outbox, Inbox      |
| v0.2    | ✅ done      | Celery delivery, Like/Announce, federated comments, photos, search, moderation, federation policy |
| v0.3    | 🔄 in progress | Frontend: homepage, recipe list, login/register forms |
| v0.4    | planned      | Nutrition module (Open Food Facts integration)          |
| v0.5    | planned      | Recipe translations, community CookedThis               |
| v0.6    | planned      | Fediverse bot (DM-based recipe submission)              |

---

## License

European Union Public Licence v1.2 (EUPL-1.2)
