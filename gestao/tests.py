from datetime import date
from decimal import Decimal
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.cache import cache
from .models import Pessoa, Cartao, Transacao, MetaMensal


class FinProSystemTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    def test_1_registro_cria_titular_e_permite_relogin_case_insensitive(self):
        from .views import gerar_token_confirmacao_email

        res = self.client.post(reverse('registro'), {
            'username': '  EduardoRosa ',
            'email': 'eduardo@finpro.app.br',
            'password1': 'FinPro@2026Seguro',
            'password2': 'FinPro@2026Seguro',
        })
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)

        user = User.objects.filter(username='eduardorosa').first()
        self.assertIsNotNone(user)

        titular = Pessoa.objects.filter(conta='eduardorosa', is_titular=True).first()
        self.assertIsNotNone(titular)

        token = gerar_token_confirmacao_email(user, user.email)
        self.client.get(reverse('confirmar_email', args=[token]))

        novo_client = Client()
        res_login = novo_client.post(reverse('login'), {
            'username': 'EDUARDOROSA ',
            'password': 'FinPro@2026Seguro',
        })
        self.assertEqual(res_login.status_code, 302)
        self.assertEqual(res_login.url, reverse('dashboard'))

    def test_2_persistencia_de_dados_apos_logout_e_novo_login(self):
        from .views import gerar_token_confirmacao_email

        self.client.post(reverse('registro'), {
            'username': 'teste_persistencia',
            'email': 'persistencia@finpro.app.br',
            'password1': 'SenhaForte@2026',
            'password2': 'SenhaForte@2026',
        })
        user = User.objects.get(username='teste_persistencia')
        token = gerar_token_confirmacao_email(user, user.email)
        self.client.get(reverse('confirmar_email', args=[token]))

        self.client.post(reverse('login'), {
            'username': 'teste_persistencia',
            'password': 'SenhaForte@2026',
        })

        titular = Pessoa.objects.get(conta='teste_persistencia', is_titular=True)
        hoje = date.today()

        self.client.post(reverse('dashboard'), {
            'form_type': 'atualizar_meta',
            'valor_meta': '3500,00'
        })
        self.client.post(reverse('dashboard'), {
            'form_type': 'nova_transacao',
            'descricao': 'Supermercado Mensal',
            'valor': '450,75',
            'data': hoje.strftime('%Y-%m-%d'),
            'categoria': 'Mercado',
            'tipo': 'DESPESA',
            'dono_id': titular.id,
            'parcelas': '1'
        })

        self.client.logout()
        sessao_dia_seguinte = Client()
        sessao_dia_seguinte.post(reverse('login'), {
            'username': 'teste_persistencia',
            'password': 'SenhaForte@2026',
        })

        res_dash = sessao_dia_seguinte.get(f"/?mes={hoje.month}&ano={hoje.year}")
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, 'Supermercado Mensal')
        self.assertEqual(float(res_dash.context['meus_gastos']), 450.75)
        self.assertEqual(float(res_dash.context['valor_meta']), 3500.0)

    def test_3_divisao_de_despesa_com_amigos_e_cobranca_whatsapp(self):
        user = User.objects.create_user(username='lider', password='123')
        self.client.force_login(user)
        titular = Pessoa.objects.create(nome='Lider', conta='lider', is_titular=True)
        amigo = Pessoa.objects.create(nome='Henrique', telefone='34999999999', conta='lider', is_titular=False)

        hoje = date.today()
        self.client.post(reverse('dashboard'), {
            'form_type': 'nova_transacao',
            'descricao': 'Churrasco',
            'valor': '200,00',
            'data': hoje.strftime('%Y-%m-%d'),
            'categoria': 'Alimentação',
            'tipo': 'DESPESA',
            'dono_id': titular.id,
            'parcelas': '1',
            'dono_id_extra': [str(amigo.id)],
            'valor_extra': ['80,00']
        })

        res = self.client.get(f"/?mes={hoje.month}&ano={hoje.year}")
        self.assertEqual(float(res.context['meus_gastos']), 120.00)
        self.assertEqual(len(res.context['dividas_detalhadas']), 1)
        self.assertEqual(float(res.context['dividas_detalhadas'][0]['saldo']), 80.00)

    def test_4_isolamento_total_entre_contas(self):
        u1 = User.objects.create_user(username='usuario1', password='123')
        u2 = User.objects.create_user(username='usuario2', password='123')
        p1 = Pessoa.objects.create(nome='U1', conta='usuario1', is_titular=True)
        t1 = Transacao.objects.create(
            descricao='Segredo U1', valor=999, data=date.today(),
            tipo='DESPESA', dono=p1, conta='usuario1'
        )

        self.client.force_login(u2)
        res = self.client.get(reverse('dashboard'))
        self.assertNotContains(res, 'Segredo U1')

        res_del = self.client.post(reverse('excluir_transacao', args=[t1.id]))
        self.assertEqual(res_del.status_code, 404)

    def test_5_importacao_fatura_csv_e_virada_de_fechamento_cartao(self):
        u = User.objects.create_user(username='importador', password='123')
        self.client.force_login(u)
        titular = Pessoa.objects.create(nome='Importador', conta='importador', is_titular=True)

        csv_conteudo = (
            "Data;Descrição;Valor\n"
            "10/10/2026;Uber Viagem;29,90\n"
            "28/10/2026;Supermercado Mart Minas;310,00\n"
            "29/10/2026;Pagamento recebido;-500,00\n"
        ).encode('latin-1')

        arquivo = SimpleUploadedFile("fatura_outubro.csv", csv_conteudo, content_type="text/csv")
        res_upload = self.client.post(reverse('dashboard'), {
            'form_type': 'upload_fatura',
            'arquivo_csv': arquivo,
        })
        self.assertEqual(res_upload.status_code, 200)
        itens_lidos = res_upload.context['transacoes_fatura']
        self.assertEqual(len(itens_lidos), 2)

        res_salvar = self.client.post(reverse('dashboard'), {
            'form_type': 'salvar_fatura_lote',
            'cartao_lote': 'NOVO_CARTAO',
            'novo_cartao_lote_nome': 'Nubank',
            'novo_cartao_fechamento': '25',
            'novo_cartao_vencimento': '5',
            'importar_chk': ['0', '1'],
            'desc_0': 'Uber Viagem', 'valor_0': '29,90', 'data_0': '2026-10-10', 'dono_0': titular.id, 'cat_0': 'Transporte',
            'desc_1': 'Supermercado Mart Minas', 'valor_1': '310,00', 'data_1': '2026-10-28', 'dono_1': titular.id, 'cat_1': 'Mercado',
        })
        self.assertEqual(res_salvar.status_code, 302)

        res_out = self.client.get('/?mes=10&ano=2026')
        self.assertContains(res_out, 'Uber Viagem')

        res_nov = self.client.get('/?mes=11&ano=2026')
        self.assertContains(res_nov, 'Supermercado Mart Minas')

    def test_6_protecao_csrf_require_post_e_lgpd_com_senha(self):
        u = User.objects.create_user(username='titular_lgpd', password='MinhaSenhaForte!2026')
        self.client.force_login(u)
        p = Pessoa.objects.create(nome='Titular', conta='titular_lgpd', is_titular=True)
        t1 = Transacao.objects.create(descricao='Item 1', valor=50, data=date.today(), tipo='DESPESA', dono=p, conta='titular_lgpd')
        Transacao.objects.create(descricao='Item 2', valor=80, data=date.today(), tipo='DESPESA', dono=p, conta='titular_lgpd')

        res_get = self.client.get(reverse('excluir_transacao', args=[t1.id]))
        self.assertEqual(res_get.status_code, 405)
        self.assertEqual(Transacao.objects.filter(conta='titular_lgpd').count(), 2)

        res_logout_get = self.client.get(reverse('logout'))
        self.assertEqual(res_logout_get.status_code, 405)

        self.client.post(reverse('excluir_transacao', args=[t1.id]))
        self.assertEqual(Transacao.objects.filter(conta='titular_lgpd').count(), 1)

        self.client.post(reverse('excluir_conta_lgpd'), {'senha_confirmacao': 'EXCLUIR'})
        self.assertTrue(User.objects.filter(username='titular_lgpd').exists())

        res_del_ok = self.client.post(reverse('excluir_conta_lgpd'), {'senha_confirmacao': 'MinhaSenhaForte!2026'})
        self.assertEqual(res_del_ok.status_code, 302)
        self.assertFalse(User.objects.filter(username='titular_lgpd').exists())

    def test_7_lancamento_inteligente_nlp_e_health_check(self):
        res_health = self.client.get(reverse('health_check'))
        self.assertEqual(res_health.status_code, 200)

        u = User.objects.create_user(username='ia_user', password='123')
        self.client.force_login(u)
        Pessoa.objects.create(nome='IA User', conta='ia_user', is_titular=True)
        Cartao.objects.create(nome='Nubank', dia_fechamento=28, dia_vencimento=5, conta='ia_user')

        res = self.client.post(reverse('dashboard'), {
            'form_type': 'lancamento_inteligente',
            'comando_ia': 'Uber Viagem 34,90 Nubank',
        })
        self.assertEqual(res.status_code, 302)
        t = Transacao.objects.filter(conta='ia_user').first()
        self.assertIsNotNone(t)
        self.assertEqual(float(t.valor), 34.90)
        self.assertEqual(t.categoria, 'Transporte')

    def test_8_regra_contabil_parcelamento_sem_perda_de_centavos(self):
        u = User.objects.create_user(username='contabil', password='123')
        self.client.force_login(u)
        Pessoa.objects.create(nome='Contabil', conta='contabil', is_titular=True)

        self.client.post(reverse('dashboard'), {
            'form_type': 'lancamento_inteligente',
            'comando_ia': 'Tenis Corrida 100,00 3x',
        })
        parcelas = list(Transacao.objects.filter(conta='contabil').order_by('data'))
        self.assertEqual(len(parcelas), 3)
        soma_exata = sum((p.valor for p in parcelas), Decimal('0.00'))
        self.assertEqual(soma_exata, Decimal('100.00'))
        self.assertEqual(parcelas[0].valor, Decimal('33.34'))
        self.assertEqual(parcelas[1].valor, Decimal('33.33'))
        self.assertEqual(parcelas[2].valor, Decimal('33.33'))

    def test_9_rate_limiting_anti_spoofing_bloqueia_forca_bruta_no_login(self):
        User.objects.create_user(username='alvo', password='SenhaCorreta!123')
        for i in range(6):
            self.client.post(
                reverse('login'),
                {'username': 'alvo', 'password': 'errada'},
                HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 200.100.50.25"
            )

        res_bloq = self.client.post(
            reverse('login'),
            {'username': 'alvo', 'password': 'SenhaCorreta!123'},
            HTTP_X_FORWARDED_FOR="192.168.1.99, 200.100.50.25"
        )
        self.assertEqual(res_bloq.status_code, 429)

    def test_10_validacao_inputs_negativos_seo_e_paginas_legais(self):
        u = User.objects.create_user(username='validador', password='123')
        self.client.force_login(u)
        titular = Pessoa.objects.create(nome='Validador', conta='validador', is_titular=True)

        self.client.post(reverse('dashboard'), {
            'form_type': 'nova_transacao',
            'descricao': 'Fraude Negativa',
            'valor': '-500,00',
            'data': date.today().strftime('%Y-%m-%d'),
            'dono_id': titular.id,
            'parcelas': 'abc'
        })
        self.assertEqual(Transacao.objects.filter(conta='validador').count(), 0)

        res_robots = self.client.get(reverse('robots_txt'))
        self.assertEqual(res_robots.status_code, 200)
        self.assertNotIn('cofre-gestao-admin', res_robots.content.decode('utf-8'))

        res_sitemap = self.client.get(reverse('sitemap_xml'))
        self.assertEqual(res_sitemap.status_code, 200)

        res_priv = self.client.get(reverse('politica_privacidade'))
        self.assertEqual(res_priv.status_code, 200)

        res_termos = self.client.get(reverse('termos_uso'))
        self.assertEqual(res_termos.status_code, 200)

    def test_11_fluxo_conta_antiga_email_confirmacao_e_redefinicao_senha(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        from .views import gerar_token_confirmacao_email, is_email_confirmado

        u_antigo = User.objects.create_user(username='cliente_antigo', password='SenhaAntiga!123')
        p_antigo = Pessoa.objects.create(nome='Cliente Antigo', conta='cliente_antigo', is_titular=True)
        Transacao.objects.create(
            descricao='Compra Antiga Preservada', valor=250, data=date.today(),
            tipo='DESPESA', dono=p_antigo, conta='cliente_antigo'
        )

        self.client.force_login(u_antigo)
        res_dash = self.client.get(reverse('dashboard'))
        self.assertContains(res_dash, 'Compra Antiga Preservada')
        self.assertContains(res_dash, 'Cadastre seu e-mail para proteger sua conta')

        self.client.post(reverse('vincular_email'), {'email': 'antigo@finpro.com'})
        u_antigo.refresh_from_db()
        self.assertEqual(u_antigo.email, 'antigo@finpro.com')
        self.assertFalse(is_email_confirmado(u_antigo))

        token_conf = gerar_token_confirmacao_email(u_antigo, 'antigo@finpro.com')
        self.client.get(reverse('confirmar_email', args=[token_conf]))
        u_antigo.refresh_from_db()
        self.assertTrue(is_email_confirmado(u_antigo))

        self.client.logout()
        res_login_email = self.client.post(reverse('login'), {
            'username': 'ANTIGO@FINPRO.COM',
            'password': 'SenhaAntiga!123'
        })
        self.assertEqual(res_login_email.status_code, 302)
        res_dash_pos = self.client.get(reverse('dashboard'))
        self.assertContains(res_dash_pos, 'Compra Antiga Preservada')

        self.client.logout()
        self.client.post(reverse('recuperar_senha'), {'identificador': 'antigo@finpro.com'})
        u_antigo.refresh_from_db()
        uidb64 = urlsafe_base64_encode(force_bytes(u_antigo.pk))
        token_reset = default_token_generator.make_token(u_antigo)

        res_reset = self.client.post(
            reverse('redefinir_senha_confirmar', kwargs={'uidb64': uidb64, 'token': token_reset}),
            {'new_password1': 'NovaSenhaForte@2026', 'new_password2': 'NovaSenhaForte@2026'}
        )
        self.assertEqual(res_reset.status_code, 302)
        self.assertEqual(res_reset.url, reverse('login'))

        res_login_nova = self.client.post(reverse('login'), {
            'username': 'antigo@finpro.com',
            'password': 'NovaSenhaForte@2026'
        })
        self.assertEqual(res_login_nova.status_code, 302)
        self.assertEqual(res_login_nova.url, reverse('dashboard'))

    def test_12_abatimento_divida_vinculada_e_avulsa_e_datas_cartao(self):
        u = User.objects.create_user(username='pagador', password='123')
        self.client.force_login(u)
        titular = Pessoa.objects.create(nome='Pagador', conta='pagador', is_titular=True)
        cartao = Cartao.objects.create(nome='Inter', dia_fechamento=25, dia_vencimento=5, conta='pagador')

        hoje = date(2026, 10, 10)
        d1 = Transacao.objects.create(
            descricao='Notebook', valor=Decimal('500.00'), data=hoje,
            tipo='DESPESA', dono=titular, cartao=cartao, conta='pagador'
        )
        Transacao.objects.create(
            descricao='Internet', valor=Decimal('300.00'), data=hoje,
            tipo='DESPESA', dono=titular, conta='pagador'
        )

        res1 = self.client.get('/?mes=10&ano=2026')
        self.assertEqual(float(res1.context['meus_gastos']), 800.00)

        self.client.post('/?mes=10&ano=2026', {
            'form_type': 'registrar_pagamento',
            'divida_id': str(d1.id),
            'valor': '200,00',
            'dono_id': str(titular.id),
        })
        res2 = self.client.get('/?mes=10&ano=2026')
        self.assertEqual(float(res2.context['meus_gastos']), 600.00)
        self.assertEqual(float(res2.context['resumo_cartoes'][0]['total_fatura']), 300.00)

        self.client.post('/?mes=10&ano=2026', {
            'form_type': 'registrar_pagamento',
            'divida_id': '',
            'valor': '150,00',
            'data': '2026-10-15',
            'dono_id': str(titular.id),
        })
        res3 = self.client.get('/?mes=10&ano=2026')
        self.assertEqual(float(res3.context['meus_gastos']), 450.00)
        self.assertEqual(float(res3.context['meus_pagamentos']), 350.00)

    def test_13_bloqueia_login_sem_email_verificado_e_permite_reenvio(self):
        from .views import gerar_token_confirmacao_email, is_email_confirmado

        # 1. Registro sem e-mail deve ser recusado
        res_sem_email = self.client.post(reverse('registro'), {
            'username': 'sem_email',
            'password1': 'SenhaForte@2026',
            'password2': 'SenhaForte@2026',
        })
        self.assertEqual(res_sem_email.status_code, 200)
        self.assertFalse(User.objects.filter(username='sem_email').exists())

        # 2. Registro com e-mail envia link e não autentica automaticamente
        mail.outbox.clear()
        res_reg = self.client.post(reverse('registro'), {
            'username': 'pendente_user',
            'email': 'pendente@finpro.app.br',
            'password1': 'SenhaForte@2026',
            'password2': 'SenhaForte@2026',
        })
        self.assertEqual(res_reg.status_code, 302)
        self.assertEqual(res_reg.url, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(len(mail.outbox), 1)

        user = User.objects.get(username='pendente_user')
        self.assertFalse(is_email_confirmado(user))

        # 3. Tentativa de login antes da confirmação é bloqueada
        novo_client = Client()
        res_bloq = novo_client.post(reverse('login'), {
            'username': 'pendente@finpro.app.br',
            'password': 'SenhaForte@2026',
        })
        self.assertEqual(res_bloq.status_code, 302)
        self.assertEqual(res_bloq.url, reverse('login'))
        self.assertNotIn('_auth_user_id', novo_client.session)

        # 4. Reenvio público dispara um novo e-mail
        res_reenvio = novo_client.post(reverse('reenviar_confirmacao_publica'))
        self.assertEqual(res_reenvio.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)

        # 5. Após confirmar pelo link, o login é liberado normalmente
        token = gerar_token_confirmacao_email(user, user.email)
        novo_client.get(reverse('confirmar_email', args=[token]))
        user.refresh_from_db()
        self.assertTrue(is_email_confirmado(user))

        res_ok = novo_client.post(reverse('login'), {
            'username': 'pendente@finpro.app.br',
            'password': 'SenhaForte@2026',
        })
        self.assertEqual(res_ok.status_code, 302)
        self.assertEqual(res_ok.url, reverse('dashboard'))


    def test_14_emails_profissionais_distintos_e_redefinicao_encerra_sessoes(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        from .views import marcar_email_como_confirmado

        # 1. Cadastro envia e-mail HTML de confirmação
        mail.outbox.clear()
        self.client.post(reverse('registro'), {
            'username': 'executivo',
            'email': 'executivo@finpro.app.br',
            'password1': 'SenhaInicial@2026',
            'password2': 'SenhaInicial@2026',
        })
        self.assertEqual(len(mail.outbox), 1)
        email_conf = mail.outbox[0]
        self.assertIn('Confirme seu e-mail', email_conf.subject)
        self.assertIn('/confirmar-email/', email_conf.body)
        self.assertEqual(len(email_conf.alternatives), 1)
        self.assertEqual(email_conf.alternatives[0][1], 'text/html')

        user = User.objects.get(username='executivo')
        marcar_email_como_confirmado(user, user.email)

        # 2. Dispositivo A mantém sessão autenticada ativa
        dispositivo_a = Client()
        dispositivo_a.post(reverse('login'), {
            'username': 'executivo@finpro.app.br',
            'password': 'SenhaInicial@2026',
        })
        self.assertEqual(dispositivo_a.get(reverse('dashboard')).status_code, 200)

        # 3. Solicitação de redefinição envia e-mail distinto (HTML + link /redefinir-senha/)
        mail.outbox.clear()
        self.client.post(reverse('recuperar_senha'), {'identificador': 'executivo@finpro.app.br'})
        self.assertEqual(len(mail.outbox), 1)
        email_reset = mail.outbox[0]
        self.assertIn('redefinição de senha', email_reset.subject.lower())
        self.assertIn('/redefinir-senha/', email_reset.body)
        self.assertEqual(len(email_reset.alternatives), 1)

        # 4. Concluir redefinição encerra sessão do Dispositivo A e envia alerta de segurança
        user.refresh_from_db()
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token_reset = default_token_generator.make_token(user)

        mail.outbox.clear()
        res_get = self.client.get(reverse('redefinir_senha_confirmar', kwargs={'uidb64': uidb64, 'token': token_reset}))
        self.assertEqual(res_get['Referrer-Policy'], 'no-referrer')

        res_post = self.client.post(
            reverse('redefinir_senha_confirmar', kwargs={'uidb64': uidb64, 'token': token_reset}),
            {'new_password1': 'NovaSenhaUltra@2026', 'new_password2': 'NovaSenhaUltra@2026'}
        )
        self.assertEqual(res_post.status_code, 302)
        self.assertEqual(res_post.url, reverse('login'))

        # Sessão do Dispositivo A foi derrubada (redireciona para /login/)
        self.assertEqual(dispositivo_a.get(reverse('dashboard')).status_code, 302)

        # E-mail de alerta de senha alterada foi disparado
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('senha foi alterada', mail.outbox[0].subject.lower())
        self.assertEqual(len(mail.outbox[0].alternatives), 1)