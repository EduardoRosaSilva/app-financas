from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from django.db.models import Q


class RegistroUsuarioForm(UserCreationForm):
    email = forms.EmailField(
        label="E-mail",
        required=True,
        help_text="Obrigatório: enviaremos um link de confirmação para liberar o seu primeiro acesso."
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "Nome de usuário"
        self.fields['username'].help_text = "Identificador único sem espaços (ex: eduardorosa)."
        self.fields['password1'].label = "Senha"
        self.fields['password1'].help_text = "Mínimo de 8 caracteres."
        self.fields['password2'].label = "Confirmar senha"
        self.fields['password2'].help_text = "Repita a senha escolhida."

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip().lower()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Este nome de usuário já está em uso.")
        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            raise forms.ValidationError("Informe um endereço de e-mail válido.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Este endereço de e-mail já está vinculado a outra conta.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = (self.cleaned_data.get('email') or '').strip().lower()
        if commit:
            user.save()
        return user


class LoginUsuarioForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "E-mail ou Nome de usuário"
        self.fields['password'].label = "Senha"

    def clean_username(self):
        identificador = self.cleaned_data.get('username', '').strip()
        if not identificador:
            return identificador
        user_obj = User.objects.filter(
            Q(username__iexact=identificador) | Q(email__iexact=identificador)
        ).first()
        return user_obj.username if user_obj else identificador.lower()


class VincularEmailForm(forms.Form):
    email = forms.EmailField(label="Seu melhor e-mail", max_length=254)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        qs = User.objects.filter(email__iexact=email)
        if self.user:
            qs = qs.exclude(pk=self.user.pk)
        if qs.exists():
            raise forms.ValidationError("Este e-mail já está em uso por outra conta.")
        return email