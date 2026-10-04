# LiftBot

SaaS platform to **hire, train, and deploy AI Employees** on client websites.

This is **not** a chatbot builder. Product UI and the website widget never use the word "chatbot".

## Stack

| Layer | Tech |
|-------|------|
| App | Django 5 + Tailwind (CDN) + MySQL |
| RAG | FastAPI + FAISS + Gemini embeddings |
| LLM | Groq → Gemini 2.0 Flash → OpenRouter |
| Queue / cache | Celery + Redis |
| Widget | Vanilla JS embed (`widget.js`) |
| Deploy (recommended) | Docker Compose on a VPS / Railway / Render / Fly.io |

## Project layout

```
liftbot/
├── docker-compose.yml      # MySQL, Redis, Django, Celery, RAG
├── .env.example
├── backend/                # Django app
├── rag/                    # FastAPI RAG engine
└── README.md
```

## Quick start (Docker)

```bash
cd ~/Projects/liftbot
cp .env.example .env
# optional: add GROQ_API_KEY / GOOGLE_API_KEY / OPENROUTER_API_KEY

docker compose up --build
```

Open:

- App: http://localhost:8001
- Admin: http://localhost:8001/admin
- RAG health: http://localhost:8101/health

> Host ports are remapped (`8001`, `8101`, `3307`, `6380`) so they do not clash with other local Docker projects. Inside the Compose network services still talk on their normal ports.

Create a superuser:

```bash
docker compose exec backend python manage.py createsuperuser
```

## Local dev without Docker (SQLite)

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DJANGO_DEBUG=1          # DEBUG is OFF by default
unset MYSQL_HOST               # blank MYSQL_HOST = SQLite (backend/db.sqlite3)
python manage.py migrate
python manage.py seed_plans    # signup needs at least one active plan
python manage.py runserver
```

Emails (signup codes, invites) print to the console unless you configure SMTP.
Run the tests with `python manage.py test apps.accounts apps.workspaces`.

### Local flow

1. Sign up (terms consent required) → verify the 6-digit email code → onboarding payment (Stripe Checkout; simulated only when `DJANGO_DEBUG=1` and no Stripe keys)
2. Hire an AI Employee
3. Train with PDF / URL / FAQ / text
4. Copy the embed snippet onto any site
5. Invite teammates from **Settings → Team members** (owner/admin only); they accept via the emailed `/invite/<token>/` link

Billing plans are seeded automatically (`Starter $19` / `Pro $49` / `Business $99`) and shown on `/pricing/`.

---

## Deploying on Vercel — important

**LiftBot cannot run as a full stack on Vercel.**

Vercel is built for static sites and serverless functions (Next.js, Node, short-lived HTTP handlers). LiftBot needs:

- long-running Django + Gunicorn  
- FastAPI RAG with **local FAISS indexes on disk**  
- **MySQL**  
- **Redis**  
- **Celery workers**  

None of those map cleanly to Vercel’s serverless model. FAISS files and Celery especially will not work there.

### What you *can* put on Vercel

| Piece | On Vercel? | Notes |
|-------|------------|-------|
| Marketing landing page | Yes | Static HTML or a small Next.js site |
| Django dashboard | No | Use Docker host instead |
| Widget `widget.js` | Yes (CDN) | Host the JS file on Vercel/CDN; API still hits your backend |
| RAG / Celery / MySQL / Redis | No | Must run elsewhere |

### Recommended production setup

1. **Backend + RAG + workers** → Docker on [Railway](https://railway.app), [Render](https://render.com), [Fly.io](https://fly.io), or any VPS (`docker compose up -d`).
2. **Managed MySQL + Redis** → Railway / PlanetScale / Redis Cloud / same Docker host.
3. **Optional marketing site** → Vercel (static), pointing CTAs to `https://app.yourdomain.com`.

### If you only want a Vercel marketing page

```bash
# example: separate tiny site later
npx create-next-app@latest liftbot-marketing
# deploy that folder to Vercel — not this Django repo
```

Connect the Vercel landing “Get started” button to your Docker-hosted LiftBot URL.

## Production deploy (VPS)

```bash
cp .env.example .env
# set a strong DJANGO_SECRET_KEY and RAG_INTERNAL_TOKEN (prod compose refuses
# to start without them), real ALLOWED_HOSTS / CSRF origins,
# PUBLIC_APP_URL=https://app.yourdomain.com, SMTP email, LLM keys, Stripe keys.
# DJANGO_DEBUG is forced to 0 by docker-compose.prod.yml.

docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser
```

Nginx listens on port 80 and proxies to Django. Put TLS (Caddy/Certbot) in front for HTTPS.

With `DJANGO_DEBUG=0` Django refuses to start on the dev secret key, sets secure
session/CSRF cookies, `nosniff` and `X-Frame-Options: DENY`. Set
`SECURE_SSL_REDIRECT=1` once HTTPS works end to end (this also enables a
1-year HSTS header). Logs go to stdout (`docker compose logs backend`); set
`ADMINS` to also receive 500-error emails.

### Stripe

1. Add `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET` to `.env`
2. Point Stripe webhook to `https://yourdomain/billing/webhook/stripe/`
3. Optional: set `stripe_price_id` on each BillingPlan in Admin
4. Without keys, onboarding payment is simulated only when `DJANGO_DEBUG=1`

---

## Environment variables

See `.env.example`. Minimum for live AI replies: at least one of `GROQ_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`. Without keys, RAG runs in offline mode: replies quote the best-matching passage from the AI Employee's training material (no generation).

## Upgrading an existing deployment

- Run migrations (`docker compose ... up` does this on boot).
- The RAG container now runs as a non-root user (uid 1000). Volumes created by the old root container need a one-off fix:
  `docker compose run --rm --user root rag chown -R 1000:1000 /app/indexes /app/uploads`
- `RAG_INTERNAL_TOKEN` is now required — set a long random value in `.env` (the RAG service refuses to start without it).
- Embeddings moved to `gemini-embedding-001`. Existing indexes are re-embedded automatically on first use per AI Employee.
- `DJANGO_DEBUG` now defaults to off; production must set `DJANGO_SECRET_KEY`.
- Stripe: point the webhook at `/billing/webhook/stripe/` and enable `checkout.session.completed`, `invoice.paid` and `customer.subscription.deleted`.

## Tests

```bash
cd backend && python manage.py test          # Django apps
cd rag && RAG_INTERNAL_TOKEN=test pytest -q tests   # RAG service
```

## Product language rule

System prompt always includes:

> You are {name}, a {role} at {company}. Never say you are an AI or a chatbot. Only answer using the provided context.
# liftbot
