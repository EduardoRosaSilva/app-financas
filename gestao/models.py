from django.db import models

class Pessoa(models.Model):
    nome = models.CharField(max_length=100)
    is_titular = models.BooleanField(default=False)  # True se for você, False se for familiar

    def __str__(self):
        return self.nome

# --- NOVO MODELO: Cartões ---
class Cartao(models.Model):
    nome = models.CharField(max_length=50) # Ex: Nubank, Inter, Mastercard Black

    def __str__(self):
        return self.nome

class Transacao(models.Model):
    TIPO_CHOICES = [
        ('DESPESA', 'Gasto no Cartão'),
        ('PAGAMENTO', 'Recebi Pagamento'),
    ]

    descricao = models.CharField(max_length=200)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    data = models.DateField()
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default='DESPESA')
    dono = models.ForeignKey(Pessoa, on_delete=models.CASCADE)
    
    # --- NOVA COLUNA: Ligação com o Cartão ---
    cartao = models.ForeignKey(Cartao, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.descricao} - R$ {self.valor}"

class MetaMensal(models.Model):
    valor = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"Meta: R$ {self.valor}"
    


    