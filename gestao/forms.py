from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User

# Formulário de Cadastro Customizado
class RegistroUsuarioForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Assumindo o controle dos textos
        self.fields['username'].label = "Nome de Usuário"
        self.fields['username'].help_text = "Crie um nome simples sem espaços (ex: eduardorosa)"
        
        self.fields['password1'].label = "Crie uma senha forte"
        self.fields['password1'].help_text = "Sua senha deve ter no mínimo 8 caracteres."
        
        self.fields['password2'].label = "Confirme sua senha"
        self.fields['password2'].help_text = "Digite a mesma senha novamente."

# Formulário de Login Customizado
class LoginUsuarioForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "Nome de Usuário"
        self.fields['password'].label = "Sua Senha"