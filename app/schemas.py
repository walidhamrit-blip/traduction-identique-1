"""Modèles de données et énumérations du microservice PDF Translator."""

from dataclasses import dataclass
from enum import Enum


class TargetLang(str, Enum):
    """Langues cibles supportées par le microservice."""

    EN = "en"
    FR = "fr"
    AR = "ar"


@dataclass(frozen=True)
class TextBlock:
    """Bloc de texte extrait d'une page PDF avec sa Bounding Box exacte.

    Système de coordonnées PDF / pdfminer.six / ReportLab :
    - Origine (0, 0) située en bas à gauche de la page.
    - x0, y0 : coin inférieur gauche (en points PDF, 1 pt = 1/72 pouce).
    - x1, y1 : coin supérieur droit.
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float = 11.0
    line_count: int = 1
    # Ligne de base réelle du texte source (origine en bas, coordonnées PDF).
    # Permet de réécrire la traduction exactement à la même hauteur.
    baseline_y: float | None = None

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True)
class ExtractedPage:
    """Représente les dimensions et les blocs de texte extraits d'une page PDF."""

    page_index: int
    width: float
    height: float
    blocks: list[TextBlock]
