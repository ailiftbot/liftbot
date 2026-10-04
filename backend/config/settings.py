from pathlib import Path
import os
import sys

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    # Safe default: production unless explicitly turned on (docker-compose.yml sets 1 for dev).
    DJANGO_DEBUG=(bool, False),
)

environ.Env.read_env(BASE_DIR.parent / '.env')

DEV_SECRET_KEY = 'dev-insecure-change-me'
SECRET_KEY = env('DJANGO_SECRET_KEY', default=DEV_SECRET_KEY)
DEBUG = env('DJANGO_DEBUG')

# `manage.py test` may use the dev key (tests always run with DEBUG off).
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == 'test'

if not DEBUG and not RUNNING_TESTS and (not SECRET_KEY or SECRET_KEY == DEV_SECRET_KEY):
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY must be set to a long random value when DJANGO_DEBUG is off.'
    )
ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])
CSRF_TRUSTED_ORIGINS = env.list('DJANGO_CSRF_TRUSTED_ORIGINS', default=[])

# Trust reverse proxy (Apache/nginx on :80 → gunicorn :8001)
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Security headers / cookies. TLS is usually terminated by nginx, so the
# redirect is opt-in (SECURE_SSL_REDIRECT=1). HSTS is only sent when the
# redirect is on, unless SECURE_HSTS_SECONDS is set explicitly.
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=False)
if not DEBUG:
    SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=True)
    CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=True)
    SECURE_HSTS_SECONDS = env.int(
        'SECURE_HSTS_SECONDS', default=31536000 if SECURE_SSL_REDIRECT else 0
    )
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'apps.accounts',
    'apps.workspaces',
    'apps.employees',
    'apps.knowledge',
    'apps.chat',
    'apps.billing',
    'apps.leads',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'apps.chat.cors.WidgetCorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'apps.workspaces.middleware.OnboardingGateMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.workspaces.context_processors.current_workspace',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

if env('MYSQL_HOST', default=''):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': env('MYSQL_DATABASE', default='liftbot'),
            'USER': env('MYSQL_USER', default='liftbot'),
            'PASSWORD': env('MYSQL_PASSWORD', default='liftbot_pass'),
            'HOST': env('MYSQL_HOST'),
            'PORT': env('MYSQL_PORT', default='3306'),
            'OPTIONS': {
                'charset': 'utf8mb4',
            },
        }
    }
else:
    # Local dev / tests without Docker.
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'home'

EMAIL_BACKEND = env(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.console.EmailBackend',
)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='LiftBot <noreply@liftbot.app>')
SERVER_EMAIL = env('SERVER_EMAIL', default=DEFAULT_FROM_EMAIL)
EMAIL_HOST = env('EMAIL_HOST', default='')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_TIMEOUT = env.int('EMAIL_TIMEOUT', default=10)

# ADMINS="Jane <jane@liftbot.app>,ops@liftbot.app"
ADMINS = []
for _entry in env.list('ADMINS', default=[]):
    _entry = _entry.strip()
    if not _entry:
        continue
    if '<' in _entry and _entry.endswith('>'):
        _name, _addr = _entry[:-1].split('<', 1)
        ADMINS.append((_name.strip() or _addr.strip(), _addr.strip()))
    else:
        ADMINS.append((_entry, _entry))

CONTACT_EMAIL_SALES = env('CONTACT_EMAIL_SALES', default='sales@liftbot.app')
CONTACT_EMAIL_PRODUCT = env('CONTACT_EMAIL_PRODUCT', default='contact@liftbot.app')
CONTACT_EMAIL_SUPPORT = env('CONTACT_EMAIL_SUPPORT', default='support@liftbot.app')
OTP_EXPIRY_MINUTES = env.int('OTP_EXPIRY_MINUTES', default=10)
OTP_RESEND_COOLDOWN_SECONDS = env.int('OTP_RESEND_COOLDOWN_SECONDS', default=60)
OTP_MAX_ATTEMPTS = env.int('OTP_MAX_ATTEMPTS', default=5)
EARLY_ACCESS_EMAIL = env('EARLY_ACCESS_EMAIL', default='ailiftbot@gmail.com')
# Public form rate limit (contact / early access): submissions per IP per hour.
PUBLIC_FORM_RATE_LIMIT = env.int('PUBLIC_FORM_RATE_LIMIT', default=5)
WORKSPACE_INVITE_EXPIRY_DAYS = 7

# Stripe (optional — without keys, billing uses manual plan assign)
STRIPE_SECRET_KEY = env('STRIPE_SECRET_KEY', default='')
STRIPE_PUBLISHABLE_KEY = env('STRIPE_PUBLISHABLE_KEY', default='')
STRIPE_WEBHOOK_SECRET = env('STRIPE_WEBHOOK_SECRET', default='')

REDIS_URL = env('REDIS_URL', default='redis://127.0.0.1:6379/0')

# Shared cache (rate limits etc.) only uses Redis when REDIS_URL is explicitly
# configured; otherwise per-process LocMem keeps local dev/tests dependency-free.
if os.environ.get('REDIS_URL'):
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'KEY_PREFIX': 'liftbot',
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'liftbot-default',
        }
    }

CELERY_TASK_ALWAYS_EAGER = env.bool('CELERY_TASK_ALWAYS_EAGER', default=False)
CELERY_TASK_EAGER_PROPAGATES = CELERY_TASK_ALWAYS_EAGER
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://127.0.0.1:6379/1')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='redis://127.0.0.1:6379/2')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

RAG_SERVICE_URL = env('RAG_SERVICE_URL', default='http://127.0.0.1:8100')
RAG_INTERNAL_TOKEN = env('RAG_INTERNAL_TOKEN', default='liftbot-rag-internal-token')

# Public URLs for website widget embed (from .env)
PUBLIC_APP_URL = env('PUBLIC_APP_URL', default='http://localhost:8001').rstrip('/')
PUBLIC_WIDGET_URL = env(
    'PUBLIC_WIDGET_URL',
    default=f'{PUBLIC_APP_URL}/static/widget.js',
)
PUBLIC_WIDGET_API_URL = env(
    'PUBLIC_WIDGET_API_URL',
    default=f'{PUBLIC_APP_URL}/api/widget',
).rstrip('/')

# Product copy rule: never say "chatbot" in UI.
PRODUCT_NAME = 'LiftBot'
PRODUCT_TAGLINE = 'Hire AI Employees for your website'

LOG_LEVEL = env('DJANGO_LOG_LEVEL', default='INFO')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s %(levelname)s %(name)s: %(message)s',
        },
    },
    'filters': {
        'require_debug_false': {'()': 'django.utils.log.RequireDebugFalse'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
            'formatter': 'standard',
        },
        'mail_admins': {
            'level': 'ERROR',
            'filters': ['require_debug_false'],
            'class': 'django.utils.log.AdminEmailHandler',
        },
    },
    'root': {'handlers': ['console'], 'level': 'WARNING'},
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.request': {
            'handlers': ['console', 'mail_admins'],
            'level': 'ERROR',
            'propagate': False,
        },
        'apps': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
        'config': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
        'celery': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}
