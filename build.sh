#!/usr/bin/env bash
# Render build script
set -o errexit

pip install -q -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input

# Render free has no shell, so the Django admin user is created from env vars.
# Needs DJANGO_SUPERUSER_USERNAME and DJANGO_SUPERUSER_PASSWORD (DJANGO_SUPERUSER_EMAIL optional).
# Safe to run on every deploy: it just fails with "already exists" the next time, which we ignore.
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  export DJANGO_SUPERUSER_EMAIL="${DJANGO_SUPERUSER_EMAIL:-admin@example.com}"
  if python manage.py createsuperuser --noinput; then
    echo "Admin user created."
  else
    echo "NOTE: admin user was not created (it probably exists already)."
  fi
fi
