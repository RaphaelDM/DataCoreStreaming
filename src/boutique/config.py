import os
from pathlib import Path


DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://olist:olist@postgres:5432/olist")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
SESSION_HTTPS_ONLY = os.environ.get("SESSION_HTTPS_ONLY", "false").lower() == "true"
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
DONNEES_ENTRANTES = Path(os.environ.get("DONNEES_ENTRANTES", "donnees/entrantes"))
MAX_CART_PRODUCTS = 20
MAX_QUANTITY = 10
PAGE_SIZE = 24
