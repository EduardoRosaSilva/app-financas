import os
import sys
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

EM_PRODUCAO = os.environ.get('RENDER') is not None
EM_TESTE = 'test' in sys.argv
DEBUG = not EM_PRODUCAO and os.environ.get('DEBUG', 'True').lower() in ('true', '1', 'yes')

# ==========================================
# 1. SECRET_KEY SEM FALLBACK EXPOSTO
# ==========================================
SECRET_KEY = os.environ.get('SECRET_KEY', '').strip()

if EM_PRODUCAO and not EM_TESTE:
    if not SECRET_KEY or SECRET_KEY.startswith('django-insecure-'):
        raise ImproperlyConfigured(
            "ERRO CRÍTICO DE SEGURANÇA: Configure a variável de ambiente SECRET_KEY no painel do Render."
        )
elif not SECRET_KEY:
    SECRET_KEY = get_random_secret_key()

# ==========================================
# 2. ALLOWED_HOSTS E CSRF_TRUSTED_ORIGINS ESTRITOS
# ==========================================
RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '').strip()

if EM_PRODUCAO and not EM_TESTE:
    hosts_env = [
        h.strip() for h in os.environ.get('ALLOWED_HOSTS', '').split(',')
        if h.strip() and h.strip() != '*'
    ]
    if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in hosts_env:
        hosts_env.append(RENDER_EXTERNAL_HOSTNAME)
    ALLOWED_HOSTS = hosts_env or ['finpro-app.onrender.com']

    origins_env = [
        o.strip() for o in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
        if o.strip() and '*' not in o
    ]
    if RENDER_EXTERNAL_HOSTNAME:
        render_origin = f"https://{RENDER_EXTERNAL_HOSTNAME}"
        if render_origin not in origins_env:
            origins_env.append(render_origin)
    CSRF_TRUSTED_ORIGINS = origins_env or ['https://finpro-app.onrender.com']
else:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']
    CSRF_TRUSTED_ORIGINS = ['http://localhost:8000', 'http://127.0.0.1:8000']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
    'gestao',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# ==========================================
# 3. BANCO DE DADOS E CACHE COMPARTILHADO ENTRE WORKERS
# ==========================================
DB_URL = os.environ.get('DATABASE_URL')

if DB_URL and not EM_TESTE:
    DATABASES = {
        'default': dj_database_url.parse(
            DB_URL,
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=EM_PRODUCAO,
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

REDIS_URL = os.environ.get('REDIS_URL', '').strip()

if REDIS_URL and not EM_TESTE:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
        }
    }
elif EM_PRODUCAO and not EM_TESTE:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': 'finpro_security_cache',
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'finpro-local-cache',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if (DEBUG or EM_TESTE)
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==========================================
# 4. SESSÕES E HEADERS DE SEGURANÇA HTTP
# ==========================================
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30  # 30 dias
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'

if EM_PRODUCAO and not EM_TESTE:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True