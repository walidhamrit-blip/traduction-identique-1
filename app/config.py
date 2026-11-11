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
# Plateforme d'exécution
# ---------------------------------------------------------------------------
# Vercel définit la variable VERCEL=1 dans ses fonctions.
IS_VERCEL = (os.getenv("VERCEL") or "").strip() == "1"

# Vercel limite le corps de requête **et de réponse** à 4,5 Mo. On se garde une
# marge (4 Mo en entrée) pour pouvoir renvoyer un message d'erreur lisible au lieu
# d'un 413 opaque de la plateforme.
VERCEL_BODY_LIMIT_BYTES = int(4.5 * 1024 * 1024)

# ---------------------------------------------------------------------------
# Limites d'entrée
# ---------------------------------------------------------------------------
MAX_UPLOAD_MB = _env_int("PDF_MAX_UPLOAD_MB", 4, minimum=1)
MAX_UPLOAD_BYTES = min(MAX_UPLOAD_MB * 1024 * 1024, VERCEL_BODY_LIMIT_BYTES - 512 * 1024)

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
# qui est déjà traduit. La valeur par défaut s'adapte à la plateforme :
# - Vercel Hobby autorise 300 s par invocation (fluide compute) ;
# - en local/Docker, on reste prudent.
TRANSLATE_TIME_BUDGET_S = _env_float(
    "PDF_TRANSLATE_BUDGET_S", 90.0 if IS_VERCEL else 35.0, minimum=5.0
)

# Marge de sécurité sous la limite Vercel de 4,5 Mo sur la réponse.
MAX_RESPONSE_BYTES = VERCEL_BODY_LIMIT_BYTES - 256 * 1024

# Langues cibles supportées par les fournisseurs gratuits
SUPPORTED_SOURCE_LANGS = ("auto", "tr", "en", "fr", "ar", "es", "de", "it", "ru", "zh", "nl")

# ---------------------------------------------------------------------------
# Béton armé pour hébergements gratuits (Render free = 512 Mo de RAM, CPU limité)
# ---------------------------------------------------------------------------
# Les polices TTF ne sont pas chargées une fois pour toutes : les garder en
# mémoire en permanence coûte ~3 Mo. On les enregistre à la demande (lazy) et on
# libère le cache après chaque requête pour éviter les dépassements mémoire.
LAZY_FONTS = (os.getenv("PDF_LAZY_FONTS", "0") or "0").strip().lower() in ("1", "true", "yes")

# Nombre de pages rasterisées simultanément pour un export PNG multi-pages.
MAX_PNG_PAGES = _env_int("PDF_MAX_PNG_PAGES", 10, minimum=1)
