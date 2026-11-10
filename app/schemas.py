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


# ---------------------------------------------------------------------------
# Options d'édition appliquées avant l'export (éditeur de l'interface web)
# ---------------------------------------------------------------------------


class TextAlign(str, Enum):
    """Alignement horizontal du texte traduit."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"  # traité comme left/right selon la langue


class VerticalAlign(str, Enum):
    """Alignement vertical dans la Bounding Box d'origine."""

    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class StyleOptions:
    """Réglages visuels appliqués à tout le document exporté."""

    font_id: str | None = None
    font_size: float | None = None  # None = taille détectée dans le PDF
    font_size_scale: float = 1.0  # multiplicateur appliqué à chaque bloc
    min_font_size: float = 5.0
    align: TextAlign = TextAlign.RIGHT
    vertical_align: VerticalAlign = VerticalAlign.MIDDLE
    color: str = "#14171E"  # couleur du texte traduit (hex)
    line_spacing: float = 1.22
    offset_x: float = 0.0  # décalage global en points (peut être négatif)
    offset_y: float = 0.0
    letter_spacing: float = 0.0
    keep_line_breaks: bool = True
    mask_padding: float = 0.8

    def resolved_font_size(self, detected: float) -> float:
        """Taille finale d'un bloc : taille choisie, sinon détectée × multiplicateur."""
        base = self.font_size if self.font_size else detected
        return max(self.min_font_size, base * self.font_size_scale)


@dataclass(frozen=True)
class BlockEdit:
    """Modification d'un bloc précis (l'utilisateur peut corriger la traduction)."""

    block_id: str
    text: str | None = None  # remplace la traduction proposée
    x0: float | None = None  # repositionnement (points PDF, origine en bas)
    y0: float | None = None
    font_size: float | None = None
    hidden: bool = False  # masque le bloc dans l'export


def block_id_of(block: TextBlock, page_index: int = 0, position: int = 0) -> str:
    """Identifiant stable d'un bloc, utilisé par l'éditeur pour le cibler.

    Basé sur la page, la position et la géométrie : reste identique entre deux
    appels pour le même PDF, ce qui permet d'enregistrer des corrections locales.
    """
    return (
        f"p{page_index}-b{position}-"
        f"{round(block.x0, 1):.1f}_{round(block.y0, 1):.1f}_"
        f"{round(block.x1, 1):.1f}_{round(block.y1, 1):.1f}"
    )
