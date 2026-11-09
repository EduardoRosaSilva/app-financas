from django.db import models


class Pessoa(models.Model):
    nome = models.CharField(max_length=100)
    telefone = models.CharField(max_length=20, blank=True, null=True)
    is_titular = models.BooleanField(default=False)
    conta = models.CharField(max_length=150)

    def __str__(self):
        return f"{self.nome} ({'Titular' if self.is_titular else 'Familiar'}) - {self.conta}"


class Cartao(models.Model):
    nome = models.CharField(max_length=100)
    dia_fechamento = models.IntegerField(default=25)
    dia_vencimento = models.IntegerField(default=5)
    conta = models.CharField(max_length=150)

    def __str__(self):
        return f"{self.nome} (Fechamento: {self.dia_fechamento})"


class Transacao(models.Model):
    TIPO_CHOICES = [
        ('DESPESA', 'Gasto no Cartão'),
        ('PAGAMENTO', 'Recebi Pagamento'),
    ]

    descricao = models.CharField(max_length=200)
    categoria = models.CharField(max_length=50, default='Outros')
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    data = models.DateField()
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default='DESPESA')
    dono = models.ForeignKey(Pessoa, on_delete=models.CASCADE)
    conta = models.CharField(max_length=255)
    recorrente = models.BooleanField(default=False)
    cartao = models.ForeignKey(Cartao, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['conta', 'data'], name='idx_conta_data'),
            models.Index(fields=['conta', 'tipo'], name='idx_conta_tipo'),
        ]

    def __str__(self):
        return f"{self.descricao} - R$ {self.valor}"


class MetaMensal(models.Model):
    valor = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    conta = models.CharField(max_length=150, unique=True)

    def __str__(self):
        return f"Meta: R$ {self.valor} ({self.conta})"