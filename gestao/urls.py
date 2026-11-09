from django.urls import path
from django.views.generic import TemplateView
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('excluir/<int:id>/', views.excluir_transacao, name='excluir_transacao'),
    path('excluir-cartao/<int:id>/', views.excluir_cartao, name='excluir_cartao'),

    # Endpoint ultraleve para manter o Render acordado gratuitamente (Keep-Alive)
    path('health/', views.health_check, name='health_check'),

    # Rotas de Autenticação e Segurança
    path('login/', views.login_view, name='login'),
    path('registro/', views.registro_view, name='registro'),
    path('logout/', views.logout_view, name='logout'),

    # Rotas LGPD, Limpeza de Dados e Relatórios
    path('exportar-csv/', views.exportar_relatorio_csv, name='exportar_relatorio_csv'),
    path('lgpd/exportar-dados/', views.exportar_dados_lgpd, name='exportar_dados_lgpd'),
    path('lgpd/limpar-dados/', views.limpar_dados_financeiros, name='limpar_dados_financeiros'),
    path('lgpd/excluir-conta/', views.excluir_conta_lgpd, name='excluir_conta_lgpd'),

    # Service Worker PWA
    path('sw.js', TemplateView.as_view(template_name='sw.js', content_type='application/javascript'), name='sw.js'),
]