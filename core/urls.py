import os
from django.contrib import admin
from django.urls import path, include

ADMIN_URL_PATH = os.environ.get('ADMIN_URL_PATH', 'cofre-gestao-admin-99/').strip('/') + '/'

urlpatterns = [
    path(ADMIN_URL_PATH, admin.site.urls),
    path('', include('gestao.urls')),
]