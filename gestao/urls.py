from django.urls import path
from . import views

urlpatterns = [
    # Quando o usuário acessar a pagina inicial vazia (''), chame a view 'dashboard'
    path('', views.dashboard, name='dashboard'),

    # O '<int:id>' avisa o Django que vamos receber um numero inteiro na URL
    path('excluir/<int:id>/', views.excluir_transacao, name='excluir_transacao'),
]