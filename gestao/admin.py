from django.contrib import admin

# O ponto (.) significa "na mesma pasta onde estou, procure o arquivo models"
from .models import Pessoa, MetaMensal, Transacao

# Aqui estamos registrando nossas classes para que o painel desenhe na tela
admin.site.register(Pessoa)
admin.site.register(MetaMensal)
admin.site.register(Transacao)

