#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate

# Script injetado para criar o Superusuário silenciosamente se ele não existir
python manage.py shell -c "import os; from django.contrib.auth.models import User; u=os.environ.get('SUPER_USER'); p=os.environ.get('SUPER_PASS'); u and p and not User.objects.filter(username=u).exists() and User.objects.create_superuser(u, '', p)"