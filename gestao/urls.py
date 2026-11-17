from django.urls import path
from django.views.generic import TemplateView
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('excluir/<int:id>/', views.excluir_transacao, name='excluir_transacao'),
    path('excluir-cartao/<int:id>/', views.excluir_cartao, name='excluir_cartao'),

    # Monitoramento e disponibilidade
    path('health/', views.health_check, name='health_check'),

    # Autenticação e Confirmação
    path('login/', views.login_view, name='login'),
    path('registro/', views.registro_view, name='registro'),
    path('logout/', views.logout_view, name='logout'),
    path('obrigado/', views.pagina_obrigado_view, name='pagina_obrigado'),

    # Páginas Legais, Compliance LGPD e Relatórios
    path('privacidade/', views.politica_privacidade_view, name='politica_privacidade'),
    path('termos/', views.termos_uso_view, name='termos_uso'),
    path('exportar-csv/', views.exportar_relatorio_csv, name='exportar_relatorio_csv'),
    path('lgpd/exportar-dados/', views.exportar_dados_lgpd, name='exportar_dados_lgpd'),
    path('lgpd/limpar-dados/', views.limpar_dados_financeiros, name='limpar_dados_financeiros'),
    path('lgpd/excluir-conta/', views.excluir_conta_lgpd, name='excluir_conta_lgpd'),

    # SEO Técnico (robots.txt e sitemap.xml) e PWA
    path('robots.txt', views.robots_txt_view, name='robots_txt'),
    path('sitemap.xml', views.sitemap_xml_view, name='sitemap_xml'),
    path('sw.js', TemplateView.as_view(template_name='sw.js', content_type='application/javascript'), name='sw.js'),
]