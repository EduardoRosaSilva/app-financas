#!/usr/bin/env bash
set -o errexit

echo "Instalando dependências..."
pip install -r requirements.txt

echo "Coletando arquivos estáticos..."
python manage.py collectstatic --no-input --clear

echo "Acordando banco Neon e aplicando migrações..."
for i in 1 2 3 4 5; do
    python manage.py migrate && break
    echo "Aguardando o compute do Neon iniciar (tentativa $i/5)..."
    sleep 4
done

echo "Criando tabela de cache compartilhado para Rate Limiting..."
for i in 1 2 3; do
    python manage.py createcachetable && break
    sleep 3
done

echo "Verificando configuração de superusuário via variáveis de ambiente..."
python manage.py shell -c "
import os
from django.contrib.auth.models import User

u = os.environ.get('SUPER_USER', '').strip()
p = os.environ.get('SUPER_PASS', '').strip()

if u and p:
    user, _ = User.objects.get_or_create(username=u)
    user.is_staff = True
    user.is_superuser = True
    user.set_password(p)
    user.save()
    print('Superusuário sincronizado com segurança.')
else:
    print('Variáveis SUPER_USER/SUPER_PASS não definidas; criação ignorada.')
"

echo "Build concluído com sucesso!"