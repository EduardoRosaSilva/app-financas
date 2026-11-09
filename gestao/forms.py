from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User


class RegistroUsuarioForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "Nome de usuário"
        self.fields['username'].help_text = "Use letras e números sem espaços (ex: eduardorosa)."
        self.fields['password1'].label = "Senha"
        self.fields['password1'].help_text = "Mínimo de 8 caracteres."
        self.fields['password2'].label = "Confirmar senha"
        self.fields['password2'].help_text = "Repita a senha escolhida."

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip().lower()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Este nome de usuário já está em uso.")
        return username


class LoginUsuarioForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "Nome de usuário"
        self.fields['password'].label = "Senha"

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        user_obj = User.objects.filter(username__iexact=username).first()
        return user_obj.username if user_obj else username.lower()