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
