# Pasticcio — Technical Specification

> A federated, open-source recipe social network built on ActivityPub.

**Version:** 0.1.0-draft  
**Status:** Pre-development / Design phase  
**License:** [EUPL-1.2](https://eupl.eu/) *(permissive, non-commercial-friendly, multilingual)*

---

## Table of Contents

1. [Project Philosophy](#1-project-philosophy)
2. [License](#2-license)
3. [Architecture Overview](#3-architecture-overview)
4. [Technology Stack](#4-technology-stack)
5. [Federation & ActivityPub](#5-federation--activitypub)
6. [Data Model](#6-data-model)
7. [Dietary Classification System](#7-dietary-classification-system)
8. [Multilingual Support](#8-multilingual-support)
9. [Nutritional Information (Optional)](#9-nutritional-information-optional)
10. ["I Made This" — Engagement Model](#10-i-made-this--engagement-model)
11. [API Design](#11-api-design)
12. [Deployment & Scalability](#12-deployment--scalability)
13. [Roadmap](#13-roadmap)

---

## 1. Project Philosophy

Pasticcio is a federated social network focused exclusively on recipes and cooking. It is designed around a few core values:

- **Federation first.** Every Pasticcio instance is a node in the broader Fediverse. Recipes, comments, and user profiles are all ActivityPub objects — a user on Mastodon can follow a recipe author on Pasticcio, boost a recipe, or comment on it, without ever creating a Pasticcio account.
- **Veg-friendly, not veg-exclusive.** Dietary filters are designed to help vegan and vegetarian users find what they need, not to exclude non-vegan content from the platform. All recipes are welcome; filters operate unidirectionally toward plant-based diets.
- **Humility over authority.** Nutritional values, dietary classifications, and health-related labels are always surfaced as author-provided metadata, never as objective facts. Pasticcio never claims to be a medical or nutritional authority.
- **Lightweight by default, scalable by design.** A single Pasticcio instance should run comfortably on a Raspberry Pi 4 for small communities, while larger deployments can scale horizontally on dedicated infrastructure without changing any code.
- **Open, forever.** The codebase is open source under a permissive, non-commercial-friendly license. No ads, no algorithmic feeds, no dark patterns.

---

## 2. License

Pasticcio is released under the **European Union Public Licence v1.2 (EUPL-1.2)**.

The EUPL-1.2 is:
- Permissive enough to allow free use, modification, and distribution
- Compatible with GPL, LGPL, AGPL, and other copyleft licenses
- Available in all 23 EU official languages (symbolically appropriate for a multilingual recipe platform)
- Explicitly non-commercial in spirit while not legally barring commercial forks (which would need to remain open source)

All contributions must be submitted under the same license (EUPL-1.2).

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Pasticcio Instance                   │
│                                                         │
│  ┌──────────────┐   ┌──────────────┐  ┌─────────────┐   │
│  │  FastAPI     │   │  Celery      │  │  Frontend   │   │
│  │  (REST +     │   │  (async      │  │  (optional  │   │
│  │  AP inbox)   │   │  federation) │  │  SPA)       │   │
│  └──────┬───────┘   └──────┬───────┘  └──────┬──────┘   │
│         │                  │                 │          │
│  ┌──────▼──────────────────▼─────────────────▼───────┐  │
│  │                   PostgreSQL                      │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  ┌──────────────┐   ┌──────────────┐                    │
│  │  Redis       │   │  Object      │                    │
│  │  (queue +    │   │  Storage     │                    │
│  │  cache)      │   │  (images)    │                    │
│  └──────────────┘   └──────────────┘                    │
└─────────────────────────────────────────────────────────┘
         │  ActivityPub (HTTP Signatures)  │
         ▼                                 ▼
  Mastodon instances             Other Pasticcio instances
  Pleroma / Akkoma               Gancio, Pixelfed, etc.
```

Components:
- **FastAPI** handles HTTP, both the REST/JSON API for the frontend and the ActivityPub inbox/outbox endpoints.
- **Celery + Redis** handle asynchronous federation tasks (sending activities to remote servers, processing incoming activities).
- **PostgreSQL** is the single source of truth for all persistent data.
- **Object storage** (local filesystem or S3-compatible) stores uploaded images.
- The **frontend** is a separate optional component (a static SPA); the backend exposes a complete API so that third-party clients and Fediverse apps (e.g. Tusky, Elk) can interact natively.

---

## 4. Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| Language | Python 3.12+ | Broad ecosystem, readable, easy contributions |
| Web framework | FastAPI | Async, Pydantic-native, OpenAPI auto-docs |
| Data validation | Pydantic v2 | Fast, strict, integrates with FastAPI |
| Database ORM | SQLAlchemy 2.x (async) | Mature, expressive, async support |
| Migrations | Alembic | Standard for SQLAlchemy projects |
| Database | PostgreSQL 15+ | Robust, full-text search, JSONB for AP objects |
| Task queue | Celery + Redis | Proven for federation workloads |
| Cache | Redis | Rate limiting, session cache, AP object cache |
| Object storage | Local FS / S3-compatible | Configurable via env vars |
| HTTP client | httpx (async) | For outgoing federation requests |
| Crypto | cryptography | HTTP Signatures for ActivityPub |
| Frontend | (TBD — likely SvelteKit or plain HTML+htmx) | Lightweight, no heavy SPA required |
| Container | Docker + Docker Compose | Standard deployment unit |
| Config | Pydantic Settings + `.env` | Twelve-factor app compliant |

### Minimum hardware targets

| Setup | Hardware | Expected users |
|---|---|---|
| Small community | Raspberry Pi 4 (4GB RAM) | < 500 active users |
| Medium instance | VPS 2 vCPU / 4GB RAM | < 5,000 active users |
| Large instance | Dedicated server / K8s | Unlimited (horizontal scale) |

---

## 5. Federation & ActivityPub

### 5.1 Supported ActivityPub objects

Pasticcio implements the ActivityPub spec (W3C) with custom extensions for recipe-specific data.

| AP Object | Pasticcio concept | Notes |
|---|---|---|
| `Person` | User profile | Standard, compatible with Mastodon |
| `Note` | Comment / "I Made This" | With `pasticcio:cookedThis` extension type |
| `Article` | Recipe | Extended with `pasticcio:recipe` properties |
| `Image` | Photo attachment | Standard |
| `Follow`, `Like`, `Announce` | Follow, Favourite, Boost | Standard |
| `Create`, `Update`, `Delete` | CRUD activities | Standard |

### 5.2 Custom namespace

All Pasticcio-specific properties live under:

```
https://pasticcio.social/ns#
```

Example context in an ActivityPub Recipe object:

```json
{
  "@context": [
    "https://www.w3.org/ns/activitystreams",
    "https://pasticcio.social/ns"
  ],
  "type": "Article",
  "pasticcio:dietaryTags": ["vegan", "gluten-free"],
  "pasticcio:metabolicTags": ["low-carb"],
  "pasticcio:servings": 4,
  "pasticcio:prepTime": "PT20M",
  "pasticcio:cookTime": "PT40M"
}
```

### 5.3 Federation model — how interaction works

**Scenario A — Mastodon user follows a Pasticcio author (no registration needed)**
1. Mastodon user searches for `@chefmaria@pasticcio.example.org`
2. Mastodon sends a `Follow` activity to Pasticcio's inbox
3. Pasticcio auto-accepts; new recipes appear in the user's Mastodon timeline as `Article` objects
4. The Mastodon user can `Like` (favourite) or `Announce` (boost) recipes from Mastodon
5. The Mastodon user can reply to a recipe; the reply appears in Pasticcio as a comment

**Scenario B — Mastodon user comments on a recipe**
1. The user replies to a boosted recipe from their Mastodon client
2. Pasticcio receives the `Note` via AP inbox
3. The note is stored as a federated comment, attributed to the remote user
4. The comment is visible on the recipe page; clicking the author name links to their remote profile

**Scenario C — Publishing a recipe (requires Pasticcio account)**
Publishing structured recipe data (ingredients, steps, nutritional info, dietary tags) requires a local Pasticcio account because the structured format cannot be inferred from a generic Fediverse `Note`. This is a deliberate, documented limitation.

> **Design note:** Full "login with Mastodon account" (OAuth-based remote authentication) is technically complex and fragile across different AP implementations. It is listed as a future optional feature (see Roadmap), not a v1 requirement.

**Scenario D — Fediverse user submits a recipe via the bot**
1. Any Fediverse user (Mastodon, Pleroma, Misskey, etc.) sends a Direct Message
   to the Pasticcio bot account (e.g. @bot@pasticcio.example.org)
2. The bot guides the user through a multi-step conversation using commands:
   - /help        shows the command guide
   - /new         starts a new recipe session
   - /title       sets the recipe title
   - /ingredients sends the ingredient list (free text, one per line)
   - /steps       sends the preparation steps (numbered)
   - /tags        sets dietary tags (e.g. vegan, gluten_free)
   - /publish     submits the recipe for review or publishes it directly
3. Session state is stored in Redis with a TTL of a few hours,
   keyed by the actor AP ID so each remote user has their own session
4. The recipe is published under a bot-managed account with attribution
   to the remote author in the description. Advanced option: create a
   local shadow account for the remote actor (more correct federatively,
   but more complex to implement)
5. A configurable approval step can require an admin to review
   bot-submitted recipes before publication

> **Design note:** This feature is deliberately deferred to after the
> frontend is functional. The bot inbox handler reuses the same AP inbox
> infrastructure already in place. The main complexity is session state
> management and the moderation workflow.

### 5.4 HTTP Signatures

All outgoing federation requests use HTTP Signatures (draft-cavage-http-signatures) with RSA-SHA256, consistent with Mastodon's implementation for maximum compatibility.

---

## 6. Data Model

### 6.1 User

```
User
├── id (UUID)
├── username (unique per instance)
├── display_name
├── bio
├── avatar_url
├── ap_id (full URI, e.g. https://instance/users/maria)
├── public_key / private_key (RSA, for HTTP Signatures)
├── is_remote (bool — remote federated users have no password)
├── remote_actor_url (for remote users)
├── preferred_language (BCP-47 code)
├── created_at / updated_at
└── settings (JSONB — UI preferences, notification settings)
```

### 6.2 Recipe

```
Recipe
├── id (UUID)
├── author → User
├── slug (URL-friendly, unique per author)
├── ap_id (full URI)
├── original_language (BCP-47)
├── status (draft | published | unlisted | deleted)
├── created_at / updated_at / published_at
│
├── translations[] → RecipeTranslation
│   ├── language (BCP-47)
│   ├── title
│   ├── description
│   ├── steps[] (ordered, rich text)
│   ├── translated_by → User (nullable, for community translations)
│   └── translation_status (original | draft | reviewed)
│
├── ingredients[] → RecipeIngredient
│   ├── sort_order
│   ├── quantity (decimal, nullable)
│   ├── unit (g | ml | tsp | tbsp | cup | piece | to_taste | ...)
│   ├── name (free text, in original language)
│   ├── food_item → FoodItem (nullable — links to nutritional DB)
│   └── notes (e.g. "finely chopped", "room temperature")
│
├── dietary_tags[] → DietaryTag (see §7)
├── metabolic_tags[] → MetabolicTag (see §7)
├── dietary_disclaimer (bool — always true if any tag set; displayed in UI)
│
├── prep_time (ISO 8601 duration, nullable)
├── cook_time (ISO 8601 duration, nullable)
├── servings (int, nullable)
├── difficulty (easy | medium | hard | nullable)
│
├── photos[] → RecipePhoto
│   ├── url
│   ├── alt_text
│   └── is_cover (bool)
│
└── ap_object (JSONB — cached raw AP representation)
```

### 6.3 RecipeTranslation

Translations are versioned and community-editable (inspired by Wikipedia's model). Each translation has an author (the translator), a status, and an edit history.

```
RecipeTranslation
├── id (UUID)
├── recipe → Recipe
├── language (BCP-47)
├── title
├── description
├── steps[] (JSONB ordered array)
├── translated_by → User (nullable)
├── translation_status (original | draft | reviewed)
├── created_at / updated_at
└── edit_history[] → TranslationEdit
    ├── editor → User
    ├── diff (JSONB)
    └── edited_at
```

### 6.4 FoodItem (Nutritional Database)

This is the optional nutritional feature. It is a separate table that can be populated from open datasets (e.g. Open Food Facts, USDA FoodData Central) or filled in manually.

```
FoodItem
├── id (UUID)
├── name (canonical, in English)
├── names (JSONB — localized names, keyed by BCP-47)
├── source (open_food_facts | usda | manual | ...)
├── source_id (external ID, nullable)
├── per_100g:
│   ├── kcal (decimal)
│   ├── protein_g (decimal)
│   ├── fat_g (decimal)
│   ├── carbs_g (decimal)
│   ├── fiber_g (decimal)
│   └── ... (extensible via JSONB for micronutrients)
└── updated_at
```

The link between a `RecipeIngredient` and a `FoodItem` is always optional and the computed nutritional summary is always shown with a disclaimer: *"Nutritional values are approximate and based on the author's ingredient mapping. Consult a nutritionist for medical dietary advice."*

### 6.5 CookedThis ("I Made This")

```
CookedThis
├── id (UUID)
├── author → User (local or remote)
├── recipe → Recipe
├── ap_id
├── content (text, nullable — free comment)
├── photos[] → CookedThisPhoto
├── variations (text, nullable — e.g. "used oat milk instead of soy")
├── created_at
└── ap_object (JSONB)
```

No ratings. No stars. No scores. A `CookedThis` is a story, not a verdict.

---

## 7. Dietary Classification System

### 7.1 Philosophy

Filters go **toward plant-based**, never away from it. There is no "omnivore" filter that excludes vegan recipes. A user can say "show me only vegan recipes", but the system will never say "hide vegan recipes".

### 7.2 Dietary tags (set by recipe author)

These are not mutually exclusive at the data level, but the UI enforces logical consistency (e.g. a recipe cannot be both "vegan" and "contains meat").

| Tag | Meaning |
|---|---|
| `vegan` | No animal products of any kind |
| `vegetarian` | No meat/fish; may contain eggs/dairy |
| `pescatarian` | No meat; may contain fish |
| `contains_meat` | Contains red meat or poultry |
| `contains_fish` | Contains fish or seafood |
| `contains_eggs` | Contains eggs |
| `contains_dairy` | Contains dairy products |
| `contains_honey` | Contains honey |

### 7.3 Allergen tags

| Tag | |
|---|---|
| `gluten_free` | No gluten-containing ingredients |
| `nut_free` | No tree nuts or peanuts |
| `soy_free` | No soy products |
| `lactose_free` | No lactose |

### 7.4 Metabolic / lifestyle tags

These are always shown with a mandatory disclaimer:

> ⚠️ *This classification is the author's own assessment and is not a medical recommendation. Consult a healthcare professional for specific dietary needs.*

| Tag | |
|---|---|
| `low_carb` | Author considers this low in carbohydrates |
| `high_protein` | Author considers this high in protein |
| `low_calorie` | Author considers this low in calories |
| `high_fiber` | Author considers this high in dietary fiber |
| `whole_food` | Author considers this a whole-food recipe |
| `raw` | No cooking required |

### 7.5 Filter logic

The filtering UI exposes **inclusive** filters only:

- "Show me: vegan / vegetarian / gluten-free / ..."
- Multiple selections are **AND**-combined by default, with optional OR mode
- There is no "exclude vegan" option anywhere in the UI

---

## 8. Multilingual Support

### 8.1 Recipe translations

Inspired by Wikipedia:
- Every recipe has a **canonical version** in the author's original language
- Community members can submit translations, which are stored separately and linked to the original
- Translations go through a simple review workflow: `draft` → `reviewed`
- The UI shows a language switcher on recipe pages, similar to Wikipedia's sidebar
- If a translation is not available in the user's preferred language, the original (or closest available) is shown with a notice

### 8.2 Interface localization

The Pasticcio UI itself is localized using standard i18n tooling (e.g. Fluent for the frontend). Instance administrators can enable/disable languages. Contributions welcome via a standard platform (e.g. Weblate).

### 8.3 Ingredient names

Ingredient names in a recipe are stored in the original language. When a `FoodItem` link exists, the nutritional database provides localized ingredient names. Free-text ingredient names (not linked to the DB) are not auto-translated — translators must provide their own ingredient names in the translation.

---

## 9. Nutritional Information (Optional)

This feature is **opt-in at the instance level** (configurable in `settings.py`) and **opt-in at the recipe level** (author must choose to enable it).

### 9.1 Data sources

Priority order:
1. **Manual entry** by the author (always possible, always authoritative)
2. **Linked FoodItem** from the local nutritional database
3. **Open Food Facts API** (if configured) — fetched on-demand and cached
4. **USDA FoodData Central** (if configured) — fallback

### 9.2 Computed summary

When nutritional data is available for all (or most) ingredients, Pasticcio can compute a per-serving summary:

- Total kcal
- Protein (g)
- Fat (g)
- Carbohydrates (g)
- Fiber (g)

This summary is always shown with:
- A coverage indicator ("Based on 7 of 9 ingredients")
- The standard disclaimer (see §7.4)
- A link to the full per-ingredient breakdown

### 9.3 Disabling the feature

Setting `ENABLE_NUTRITION=false` in the instance configuration:
- Hides all nutritional UI elements
- Disables FoodItem API calls
- Does not delete existing nutritional data (re-enabling restores it)

---

## 10. "I Made This" — Engagement Model

Pasticcio rejects star ratings and numeric scores. Taste is subjective; a 3-star rating tells you nothing about whether *you* will enjoy a dish.

Instead, Pasticcio has **CookedThis** — a way for users to say they tried a recipe and share their experience.

A CookedThis entry can include:
- A text comment (optional)
- Photos of the result (optional)
- Notes on variations used ("I replaced butter with coconut oil")

CookedThis entries are **ActivityPub objects** (`Note` with type extension) and are fully federated. A Mastodon user who replies to a recipe will have their reply displayed as a federated CookedThis (with degraded structure — no variation notes unless submitted via Pasticcio).

There are no upvotes, no helpful/not-helpful buttons, no ranking of comments. Entries are shown chronologically.

---

## 11. API Design

### 11.1 REST API (internal)

The frontend communicates with the backend via a REST JSON API at `/api/v1/`. Full OpenAPI docs are auto-generated and served at `/api/docs`.

Key endpoint groups:
- `POST /api/v1/auth/` — registration, login (JWT), token refresh
- `GET|POST /api/v1/recipes/` — list and create recipes
- `GET|PUT|DELETE /api/v1/recipes/{id}/` — recipe detail, update, delete
- `GET|POST /api/v1/recipes/{id}/translations/` — list and submit translations
- `GET|POST /api/v1/recipes/{id}/cooked/` — list and submit CookedThis entries
- `GET /api/v1/users/{username}/` — user profile
- `GET /api/v1/explore/` — public feed, filterable by dietary tags
- `GET /api/v1/search/` — full-text recipe search

### 11.2 ActivityPub endpoints

Following the AP spec:
- `GET /.well-known/webfinger` — WebFinger for user discovery
- `GET /users/{username}` — Actor object
- `GET /users/{username}/inbox` — Inbox (POST for incoming activities)
- `GET /users/{username}/outbox` — Outbox (paginated)
- `GET /users/{username}/followers` — Followers collection
- `GET /users/{username}/following` — Following collection
- `GET /recipes/{id}` — Recipe as AP Article object

---

## 12. Deployment & Scalability

### 12.1 Single-server (Raspberry Pi / small VPS)

```yaml
# docker-compose.yml (simplified)
services:
  web:       # FastAPI via Uvicorn
  worker:    # Celery worker
  db:        # PostgreSQL
  redis:     # Redis
  caddy:     # Reverse proxy with automatic HTTPS
```

All services run on a single machine. Images stored on local filesystem. This is the default and documented deployment path.

### 12.2 Scaled deployment

For larger instances, the same components scale horizontally:
- Multiple `web` replicas behind a load balancer
- Multiple `worker` replicas for federation throughput
- PostgreSQL with read replicas (SQLAlchemy async handles this transparently)
- Redis Cluster or Redis Sentinel for HA
- S3-compatible object storage (MinIO, Backblaze B2, AWS S3)

No code changes are needed between small and large deployments — only environment variables differ.

### 12.3 Configuration

All configuration is via environment variables (twelve-factor). A `.env.example` ships with the repository. Key variables:

```
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://...
SECRET_KEY=...
INSTANCE_DOMAIN=pasticcio.example.org
INSTANCE_NAME=My Pasticcio
ENABLE_NUTRITION=true
ENABLE_REGISTRATIONS=true
STORAGE_BACKEND=local  # or s3
S3_BUCKET=...
S3_ENDPOINT=...
```

---

## 13. Roadmap

### v0.1 — Foundation
- [ ] Project scaffolding (FastAPI, PostgreSQL, Alembic, Docker Compose)
- [ ] User model + auth (JWT)
- [ ] Recipe model + CRUD API
- [ ] Basic dietary tagging
- [ ] WebFinger + Actor AP endpoints

### v0.2 — Federation
- [ ] AP inbox/outbox
- [ ] Follow/Unfollow
- [ ] Federated recipe delivery
- [ ] Incoming comments from Mastodon

### v0.3 — Community features
- [ ] Recipe translations
- [ ] CookedThis entries
- [ ] Photo uploads

### v0.4 — Nutrition (optional module)
- [ ] FoodItem database + seeding from Open Food Facts
- [ ] Nutritional summary on recipes
- [ ] Manual entry support

### v0.5 — Frontend
- [ ] Public recipe browser
- [ ] Author dashboard
- [ ] Mobile-responsive

### v0.6 — Fediverse bot
- [ ] Bot account (@bot@instance) with its own AP Actor and inbox
- [ ] Command parser for DM-based recipe submission
      (/new, /title, /ingredients, /steps, /tags, /publish, /help)
- [ ] Redis session state management per remote actor (with TTL)
- [ ] Optional admin approval step for bot-submitted recipes
- [ ] Attribution of the remote Fediverse author in the published recipe

### Future / under evaluation
- [ ] Remote login (OAuth with Mastodon) — complex, deferred
- [ ] Full-text multilingual search (PostgreSQL FTS or Meilisearch)
- [ ] Ingredient unit conversion
- [ ] Meal planner (stretch goal)

---

*Pasticcio is an independent open-source project. It is not affiliated with any commercial entity.*
