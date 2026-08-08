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

### Local flow

1. Sign up → workspace created on Starter plan  
2. Hire an AI Employee  
3. Train with PDF / URL / FAQ / text  
4. Copy the embed snippet onto any site  
5. Test in Playground  

Billing plans are seeded automatically (`Starter $19` / `Pro $49` / `Business $99`). Assign plans in Django Admin for MVP.

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
# set DJANGO_DEBUG=0, strong DJANGO_SECRET_KEY, real ALLOWED_HOSTS / CSRF,
# PUBLIC_APP_URL=https://app.yourdomain.com, LLM keys, optional Stripe

docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser
```

Nginx listens on port 80 and proxies to Django. Put TLS (Caddy/Certbot) in front for HTTPS.

### Stripe

1. Add `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET` to `.env`
2. Point Stripe webhook to `https://yourdomain/billing/webhook/stripe/`
3. Optional: set `stripe_price_id` on each BillingPlan in Admin
4. Without keys, **Select plan** on Billing still works manually

---

## Environment variables

See `.env.example`. Minimum for live AI replies: at least one of `GROQ_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`. Without keys, RAG runs in offline demo mode.

## Product language rule

System prompt always includes:

> You are {name}, a {role} at {company}. Never say you are an AI or a chatbot. Only answer using the provided context.
# liftbot
