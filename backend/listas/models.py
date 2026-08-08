from django.db import models

class Mercado(models.Model):
    nome = models.CharField(max_length=100)

    def __str__(self):
        return self.nome


class Lista(models.Model):
    nome = models.CharField(max_length=100)
    mercado = models.ForeignKey(Mercado, on_delete=models.SET_NULL, null=True, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome


class Item(models.Model):
    lista = models.ForeignKey(Lista, on_delete=models.CASCADE, related_name="itens")
    nome = models.CharField(max_length=100)
    quantidade = models.CharField(max_length=50, blank=True)
    preco = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    comprado = models.BooleanField(default=False)
    ordem = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.nome


class HistoricoPreco(models.Model):
    item_nome = models.CharField(max_length=100)
    mercado = models.ForeignKey(Mercado, on_delete=models.CASCADE)
    preco = models.DecimalField(max_digits=8, decimal_places=2)
    data = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"{self.item_nome} - {self.mercado} - R${self.preco}"
    