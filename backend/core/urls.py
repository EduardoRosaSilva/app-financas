from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # Painel de Admin oculto contra robôs exploradores
    path('cofre-admin-secreto-99/', admin.site.urls),
    
    # Repassa tudo para o app gestao
    path('', include('gestao.urls')), 
]