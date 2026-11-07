from datetime import date
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from .models import Pessoa, Cartao, Transacao, MetaMensal


class FinProSystemTests(TestCase):
    def setUp(self):
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

        # Garante que a Pessoa titular foi criada automaticamente
        titular = Pessoa.objects.filter(conta='eduardorosa', is_titular=True).first()
        self.assertIsNotNone(titular)

        # Faz logout e tenta logar em uma nova sessão com letras maiúsculas e espaço
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

        # Cadastra meta e transação
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

        # Simula fechar o navegador e logar no dia seguinte
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
        # Titular fica com 120.00 e amigo deve 80.00
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

        # Tentativa de excluir transação do usuario1 logado como usuario2 deve retornar 404
        res_del = self.client.get(reverse('excluir_transacao', args=[t1.id]))
        self.assertEqual(res_del.status_code, 404)