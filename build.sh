#!/usr/bin/env bash
set -o errexit

echo "📦 Instalando dependências..."
pip install -r requirements.txt

echo "🎨 Coletando arquivos visuais do Admin..."
python manage.py collectstatic --no-input --clear

echo "🗄️ Construindo tabelas no PostgreSQL oficial..."
python manage.py migrate

echo "👑 Configurando conta de superusuário de emergência..."
python manage.py shell -c "import os; from django.contrib.auth.models import User; u=os.environ.get('SUPER_USER', 'eduardoadmin'); p=os.environ.get('SUPER_PASS', 'Finpro@2026'); not User.objects.filter(username=u).exists() and User.objects.create_superuser(u, '', p)"

echo "✅ Sistema pronto para produção!"