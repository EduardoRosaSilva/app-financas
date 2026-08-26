import os
import sys
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# Chave de segurança padrão (usada apenas em ambiente local)
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-07_6yo3%t_3x%+os(g+23f_+zowfjm5j_co_+y@mmnss9v7gz+')

# DEBUG desligado automaticamente na nuvem para máxima segurança
DEBUG = os.environ.get('RENDER') is None

ALLOWED_HOSTS = ['*']

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
    'gestao.middleware.ContaInvisivelMiddleware', 
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
# BANCO DE DADOS (FIM DA AMNÉSIA)
# ==========================================
DB_URL = os.environ.get('DATABASE_URL')

if os.environ.get('RENDER') and DB_URL:
    # PRODUÇÃO: Força o uso do PostgreSQL da Nuvem
    DATABASES = {
        'default': dj_database_url.parse(DB_URL, conn_max_age=600, ssl_require=True)
    }
else:
    # LOCAL: Mantém o SQLite apenas para testes no seu computador
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

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# ==========================================
# ARQUIVOS ESTÁTICOS
# ==========================================
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==========================================
# SEGURANÇA E SESSÕES BANCÁRIAS (COMPLIANCE)
# ==========================================
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    
    # Desloga o usuário ao fechar o navegador
    SESSION_EXPIRE_AT_BROWSER_CLOSE = True 
    # Expira a sessão após 30 minutos de inatividade (1800 segundos)
    SESSION_COOKIE_AGE = 1800 
    # Renova o tempo a cada ação dentro do sistema
    SESSION_SAVE_EVERY_REQUEST = True