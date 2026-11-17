from django.core.management.base import BaseCommand
from gestao.models import Cartao


class Command(BaseCommand):
    help = 'Cadastra os principais cartões do Brasil vinculados a uma conta específica.'

    def add_arguments(self, parser):
        parser.add_argument('--conta', type=str, required=True, help='Username do titular da conta')

    def handle(self, *args, **options):
        conta_alvo = options['conta'].strip().lower()
        cartoes_brasil = [
            "Nubank",
            "Banco Inter",
            "C6 Bank",
            "Itaú",
            "Bradesco",
            "Santander",
            "Banco do Brasil",
            "Caixa Econômica Federal",
            "XP Visa Infinite",
            "BTG Pactual",
            "PicPay Card",
            "Mercado Pago",
        ]

        criados = 0
        for nome_cartao in cartoes_brasil:
            _, criado = Cartao.objects.get_or_create(nome=nome_cartao, conta=conta_alvo)
            if criado:
                criados += 1

        self.stdout.write(self.style.SUCCESS(f'Sucesso! {criados} cartões cadastrados para a conta {conta_alvo}.'))