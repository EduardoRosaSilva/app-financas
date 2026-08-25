from django.urls import path
from django.views.generic import TemplateView
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('excluir/<int:id>/', views.excluir_transacao, name='excluir_transacao'),
    
    # Rotas de Segurança
    path('login/', views.login_view, name='login'),
    path('registro/', views.registro_view, name='registro'),
    path('logout/', views.logout_view, name='logout'),
    
    # ROTA RAIZ PARA O PWA FUNCIONAR (NOVO)
    path('sw.js', TemplateView.as_view(template_name='sw.js', content_type='application/javascript'), name='sw.js'),
]