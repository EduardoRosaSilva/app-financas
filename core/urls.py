from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('gestao.urls')),   # <-- Diz para o Django: "Tudo que for pra página inicial, mande para as rotas do app gestao"
]
