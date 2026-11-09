from datetime import date
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
        """Testa se o usuário criado consegue deslogar e logar novamente mesmo digitando maiúsculas ou espaços."""
        res = self.client.post(reverse('registro'), {
            'username': '  EduardoRosa ',
            'password1': 'FinPro@2026Seguro',
            'password2': 'FinPro@2026Seguro',
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue(User.objects.filter(username='eduardorosa').exists())

        titular = Pessoa.objects.filter(conta='eduardorosa', is_titular=True).first()
        self.assertIsNotNone(titular)

        self.client.logout()
        novo_client = Client()
        res_login = novo_client.post(reverse('login'), {
            'username': 'EDUARDOROSA ',
            'password': 'FinPro@2026Seguro',
        })
        self.assertEqual(res_login.status_code, 302)
        self.assertEqual(res_login.url, reverse('dashboard'))

    def test_2_persistencia_de_dados_apos_logout_e_novo_login(self):
        """Garante que transações, cartões e metas não somem após encerrar a sessão e logar novamente."""
        self.client.post(reverse('registro'), {
            'username': 'teste_persistencia',
            'password1': 'SenhaForte@2026',
            'password2': 'SenhaForte@2026',
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
        """Testa a divisão de uma compra entre o titular e um amigo e o cálculo de dívidas."""
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
        """Garante que um usuário nunca veja nem exclua transações de outro usuário."""
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

        res_del = self.client.get(reverse('excluir_transacao', args=[t1.id]))
        self.assertEqual(res_del.status_code, 404)

    def test_5_importacao_fatura_csv_e_virada_de_fechamento_cartao(self):
        """Testa upload de fatura CSV com compras antes e depois do fechamento (dia 25) e criação de cartão no lote."""
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
        # Deve ler os 2 gastos e ignorar 'Pagamento recebido'
        self.assertEqual(len(itens_lidos), 2)
        self.assertEqual(itens_lidos[0]['categoria'], 'Transporte')
        self.assertEqual(itens_lidos[1]['categoria'], 'Mercado')

        # Salva o lote criando o cartão Nubank (fechamento dia 25) na hora
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

        # Compra de 10/10 deve aparecer na fatura 10/2026
        res_out = self.client.get('/?mes=10&ano=2026')
        self.assertContains(res_out, 'Uber Viagem')

        # Compra de 28/10 (após fechamento dia 25) deve aparecer na fatura 11/2026!
        res_nov = self.client.get('/?mes=11&ano=2026')
        self.assertContains(res_nov, 'Supermercado Mart Minas')

    def test_6_lgpd_portabilidade_json_e_direito_ao_esquecimento(self):
        """Testa exportação de dados LGPD (JSON) e exclusão total da conta mediante senha."""
        u = User.objects.create_user(username='titular_lgpd', password=' MinhaSenha123 ')
        self.client.force_login(u)
        p = Pessoa.objects.create(nome='Titular', conta='titular_lgpd', is_titular=True)
        Transacao.objects.create(descricao='Farmacia', valor=50, data=date.today(), tipo='DESPESA', dono=p, conta='titular_lgpd')

        # 1. Portabilidade JSON
        res_json = self.client.get(reverse('exportar_dados_lgpd'))
        self.assertEqual(res_json.status_code, 200)
        self.assertIn('titular_lgpd', res_json.content.decode('utf-8'))

        # 2. Exclusão total da conta
        res_del = self.client.post(reverse('excluir_conta_lgpd'), {'senha_confirmacao': ' MinhaSenha123 '})
        self.assertEqual(res_del.status_code, 302)
        self.assertFalse(User.objects.filter(username='titular_lgpd').exists())
        self.assertEqual(Transacao.objects.filter(conta='titular_lgpd').count(), 0)