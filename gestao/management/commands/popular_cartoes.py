from django.core.management.base import BaseCommand
from gestao.models import Cartao

class Command(BaseCommand):
    help = 'Cadastra os principais cartões e bancos do Brasil automaticamente.'

    def handle(self, *args, **options):
        # Lista dos principais emissores do mercado brasileiro
        cartoes_brasil = [
            "Nubank (Roxinho)",
            "Nubank Ultravioleta",
            "Banco Inter",
            "C6 Bank",
            "Itaú (Credicard / Personnalité)",
            "Bradesco",
            "Santander",
            "Banco do Brasil",
            "Caixa Econômica Federal",
            "XP Visa Infinite",
            "BTG Pactual",
            "Neon",
            "PicPay Card",
            "Mercado Pago",
            "Ágora / Bradesco Cartões",
            "Next",
            "Original"
        ]

        criados = 0
        for nome_cartao in cartoes_brasil:
            # get_or_create evita duplicar caso o comando seja rodado mais de uma vez
            cartao, criado = Cartao.objects.get_or_create(nome=nome_cartao)
            if criado:
                criados += 1

        self.stdout.write(self.style.SUCCESS(f'Sucesso! {criados} cartões do Brasil foram cadastrados no banco de dados.'))