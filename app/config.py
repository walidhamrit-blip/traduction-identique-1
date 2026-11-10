"""Configuration centralisée, pilotable par variables d'environnement.

Toutes les limites sont volontairement prudentes par défaut afin de rester
sous les contraintes du plan gratuit Vercel (payload HTTP ≈ 4,5 Mo, 1 Go de
RAM, 60 s d'exécution par invocation).
"""

from __future__ import annotations

import os
from pathlib import Path

# Racine du projet (…/traduction-identique-1)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Lit un entier depuis l'environnement en ignorant les valeurs invalides."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.1) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, float(raw.strip()))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Limites d'entrée
# ---------------------------------------------------------------------------
# Vercel rejette les requêtes > 4,5 Mo AVANT d'atteindre la fonction (413).
# On reste donc à 4 Mo par défaut pour pouvoir renvoyer un message clair.
MAX_UPLOAD_MB = _env_int("PDF_MAX_UPLOAD_MB", 4, minimum=1)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# Garde-fou mémoire/CPU : au-delà, l'invocation dépasse 60 s ou 1 Go de RAM.
MAX_PAGES = _env_int("PDF_MAX_PAGES", 40, minimum=1)

# Nombre maximal de blocs texte traités par page (protection PDFs pathologiques).
MAX_BLOCKS_PER_PAGE = _env_int("PDF_MAX_BLOCKS_PER_PAGE", 4000, minimum=50)

# ---------------------------------------------------------------------------
# Divers
# ---------------------------------------------------------------------------
SERVICE_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# Traduction (gratuite, sans clé API)
# ---------------------------------------------------------------------------
# Ordre des fournisseurs essayés (voir app/translation/chain.py).
TRANSLATOR_PROVIDER = os.getenv("TRANSLATOR_PROVIDER", "auto").strip() or "auto"

# Langue source : "auto" = détection par le fournisseur (recommandé).
SOURCE_LANG = os.getenv("PDF_SOURCE_LANG", "auto").strip() or "auto"

# Traduction activée par défaut ; PDF_TRANSLATE=0 renvoie le PDF nettoyé sans traduction.
TRANSLATE_ENABLED = (os.getenv("PDF_TRANSLATE", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

# Budget temps : au-delà, le pipeline cesse d'appeler le réseau et assemble ce
# qui est déjà traduit (Vercel coupe la fonction à 60 s).
TRANSLATE_TIME_BUDGET_S = _env_float("PDF_TRANSLATE_BUDGET_S", 35.0, minimum=5.0)

# Langues cibles supportées par les fournisseurs gratuits
SUPPORTED_SOURCE_LANGS = ("auto", "tr", "en", "fr", "ar", "es", "de", "it", "ru", "zh", "nl")
