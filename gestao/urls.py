from django.urls import path
from django.views.generic import TemplateView
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('excluir/<int:id>/', views.excluir_transacao, name='excluir_transacao'),

    # Rotas de Autenticação e Segurança
    path('login/', views.login_view, name='login'),
    path('registro/', views.registro_view, name='registro'),
    path('logout/', views.logout_view, name='logout'),

    # Rotas LGPD e Relatórios (Portabilidade, Exportação CSV e Direito ao Esquecimento)
    path('exportar-csv/', views.exportar_relatorio_csv, name='exportar_relatorio_csv'),
    path('lgpd/exportar-dados/', views.exportar_dados_lgpd, name='exportar_dados_lgpd'),
    path('lgpd/excluir-conta/', views.excluir_conta_lgpd, name='excluir_conta_lgpd'),

    # Service Worker PWA
    path('sw.js', TemplateView.as_view(template_name='sw.js', content_type='application/javascript'), name='sw.js'),
]