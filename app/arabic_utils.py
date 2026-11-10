"""Formatage du texte arabe (liaison contextuelle des glyphes + algorithme BiDi RTL)."""

import arabic_reshaper
from bidi.algorithm import get_display


def reshape_arabic(text: str) -> str:
    """Applique uniquement le reshaping contextuel arabe (formes isolée/initiale/médiane/finale).

    Utile pour mesurer la largeur exacte des glyphes et ligatures (ex: Lam-Alef)
    dans ReportLab avant le découpage multi-lignes.
    """
    if not text or not text.strip():
        return text
    return arabic_reshaper.reshape(text)


def format_arabic_text(text: str) -> str:
    """Formate une ligne contenant de l'arabe pour un rendu fidèle dans ReportLab.

    Étapes obligatoires :
    1. `arabic_reshaper.reshape` convertit les caractères Unicode arabes standards
       (U+0600..U+06FF) en Arabic Presentation Forms (U+FB50..U+FDFF / U+FE70..U+FEFF)
       afin d'assurer la liaison cursive des lettres et ligatures.
    2. `bidi.algorithm.get_display` applique l'algorithme bidirectionnel Unicode (UAX #9)
       pour inverser l'ordre visuel de droite à gauche (RTL) tout en conservant
       l'ordre gauche-à-droite des nombres et segments latins intégrés.
    """
    if not text or not text.strip():
        return text
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)
