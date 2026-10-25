"""Render · gunicorn 진입점: gunicorn -c gunicorn.conf.py wsgi:app"""
from server import create_app

app = create_app()
