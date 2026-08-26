#!/usr/bin/env bash
set -o errexit

echo "📦 Instalando dependencias..."
pip install -r requirements.txt

echo "🎨 Coletando arquivos estaticos (CSS do Admin)..."
python manage.py collectstatic --no-input --clear

echo "🗄️ Aplicando tabelas no PostgreSQL oficial..."
python manage.py migrate

echo "✅ Build concluido!"