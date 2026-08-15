from django.db import models
import uuid

class Conta(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Conta Anonima - {str(self.id)[:8]}"

class Pessoa(models.Model):
    nome = models.CharField(max_length=100)
    telefone = models.CharField(max_length=20, blank=True, null=True) # NOVO CAMPO AQUI!
    is_titular = models.BooleanField(default=False)
    conta = models.CharField(max_length=255) # O nome de usuário dono deste registro

    def __str__(self):
        return self.nome

# --- NOVO MODELO: Cartões ---
class Cartao(models.Model):
    nome = models.CharField(max_length=50) # Ex: Nubank, Inter, Mastercard Black
    conta = models.CharField(max_length=255)

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
    conta = models.CharField(max_length=255)
    recorrente = models.BooleanField(default=False)  # NOVO: Para despesas fixas mensais
    
    # --- NOVA COLUNA: Ligação com o Cartão ---
    cartao = models.ForeignKey(Cartao, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.descricao} - R$ {self.valor}"

class MetaMensal(models.Model):
    valor = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    conta = models.CharField(max_length=255)

    def __str__(self):
        return f"Meta: R$ {self.valor}"
    


    