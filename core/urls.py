import os
from django.contrib import admin
from django.urls import path, include
from gestao.views import admin_login_protegido

ADMIN_URL_PATH = os.environ.get('ADMIN_URL_PATH', 'cofre-gestao-admin-99').strip('/')

urlpatterns = [
    path(f'{ADMIN_URL_PATH}/login/', admin_login_protegido, name='admin_login_protegido'),
    path(f'{ADMIN_URL_PATH}/', admin.site.urls),
    path('', include('gestao.urls')),
]

handler404 = 'gestao.views.erro_404_personalizado'
handler500 = 'gestao.views.erro_500_personalizado'