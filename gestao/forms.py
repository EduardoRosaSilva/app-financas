from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User


class RegistroUsuarioForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "Nome de Usuário"
        self.fields['username'].help_text = "Crie um nome simples sem espaços (ex: eduardorosa)"
        self.fields['password1'].label = "Crie uma senha forte"
        self.fields['password1'].help_text = "Sua senha deve ter no mínimo 8 caracteres."
        self.fields['password2'].label = "Confirme sua senha"
        self.fields['password2'].help_text = "Digite a mesma senha novamente."

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip().lower()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Este nome de usuário já está cadastrado.")
        return username


class LoginUsuarioForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "Nome de Usuário"
        self.fields['password'].label = "Sua Senha"

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        user_obj = User.objects.filter(username__iexact=username).first()
        return user_obj.username if user_obj else username.lower()