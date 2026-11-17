from django.urls import path
from django.views.generic import TemplateView
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('excluir/<int:id>/', views.excluir_transacao, name='excluir_transacao'),
    path('excluir-cartao/<int:id>/', views.excluir_cartao, name='excluir_cartao'),

    # Monitoramento e disponibilidade
    path('health/', views.health_check, name='health_check'),

    # Autenticação, Confirmação de E-mail e Redefinição de Senha
    path('login/', views.login_view, name='login'),
    path('registro/', views.registro_view, name='registro'),
    path('logout/', views.logout_view, name='logout'),
    path('conta/vincular-email/', views.vincular_email_view, name='vincular_email'),
    path('conta/reenviar-confirmacao/', views.reenviar_confirmacao_email_view, name='reenviar_confirmacao_email'),
    path('confirmar-email/<str:token>/', views.confirmar_email_view, name='confirmar_email'),
    path('recuperar-senha/', views.recuperar_senha_view, name='recuperar_senha'),
    path('redefinir-senha/<uidb64>/<token>/', views.redefinir_senha_confirmar_view, name='redefinir_senha_confirmar'),
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