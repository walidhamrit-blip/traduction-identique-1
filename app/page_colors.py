"""Analyse visuelle de la page : couleur du fond et du texte d'origine.

Pourquoi ce module
------------------
Le calque de traduction recouvrait le texte d'origine par des rectangles **blancs**.
Sur un document à fonds colorés (bandeaux gris, cellules surlignées, textes blancs
sur fond sombre), cela produit des « trous » blancs très visibles : la mise en forme
n'est plus identique à l'original.

Ce module mesure, dans le rendu réel de la page, la couleur dominante du **fond**
autour du bloc et la couleur de l'**encre** du texte d'origine. Le masque reprend
alors exactement la couleur du fond, et le texte traduit garde la couleur d'origine
(texte blanc sur fond gris, par exemple).

Repères
-------
- pdfminer, le rendu de l'aperçu et le calque ReportLab partagent le même repère
  (origine en bas à gauche). Les pixels d'un rendu PyMuPDF ont, eux, l'origine en
  haut à gauche : la conversion se fait ici, une fois pour toutes.
- Tout est facultatif : si PyMuPDF est absent ou si l'analyse échoue, on retombe
  sur les couleurs configurées par défaut.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Rendu suffisamment fin pour distinguer un fond de cellule sans coûter cher.
ANALYSIS_DPI = 100
# Marge (points) entre le bloc et la zone où l'on cherche le fond.
BACKGROUND_MARGIN = 2.5
# Écart minimal (0-255, distance par canal) pour distinguer l'encre du fond.
INK_DISTANCE_MIN = 50.0
# Part minimale de la couleur dominante pour considérer le fond comme uniforme.
BACKGROUND_SHARE_MIN = 0.45
# Quantification des couleurs pour l'histogramme (tolérance aux dégradés légers).
COLOR_BUCKET = 12


@dataclass(frozen=True)
class BlockColors:
    """Couleurs observées pour un bloc : fond de la zone et encre d'origine."""

    background: tuple[float, float, float] | None = None
    ink: tuple[float, float, float] | None = None

    @property
    def has_background(self) -> bool:
        return self.background is not None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _to_unit(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    return (_clamp(rgb[0] / 255.0), _clamp(rgb[1] / 255.0), _clamp(rgb[2] / 255.0))


def _luminance(rgb: tuple[int, int, int]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


class PageAnalysis:
    """Analyse paresseuse d'une page : un seul rendu, plusieurs mesures."""

    def __init__(self, pixmap, page_width_pt: float) -> None:
        self._pixmap = pixmap
        self._n = pixmap.n
        self._samples = pixmap.samples
        self._stride = pixmap.stride
        # Échelle pixels/point, déduite de la largeur réelle de la page.
        self._page_width_pt = max(1.0, float(page_width_pt))
        self._scale = pixmap.width / self._page_width_pt
        self._page_height_pt = max(1.0, pixmap.height / self._scale)

    # -- conversions -------------------------------------------------------
    def _pixel(self, px: int, py: int) -> tuple[int, int, int]:
        if px < 0 or py < 0 or px >= self._pixmap.width or py >= self._pixmap.height:
            return (255, 255, 255)
        offset = py * self._stride + px * self._n
        chunk = self._samples[offset : offset + self._n]
        if len(chunk) >= 3:
            return (chunk[0], chunk[1], chunk[2])
        if len(chunk) == 1:
            return (chunk[0], chunk[0], chunk[0])
        return (255, 255, 255)

    def _to_pixels(self, rect: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        """Convertit un rectangle « repère PDF » (origine en bas) en indices de pixels."""
        x0, y0, x1, y1 = rect
        height_pt = self._page_height_pt
        left = max(0, int(x0 * self._scale))
        right = min(self._pixmap.width, int(round(x1 * self._scale)))
        # Origine en haut pour les pixels : y_pixel = hauteur_page - y_pdf
        top = max(0, int((height_pt - y1) * self._scale))
        bottom = min(self._pixmap.height, int(round((height_pt - y0) * self._scale)))
        return left, top, max(left + 1, right), max(top + 1, bottom)

    # -- mesures -----------------------------------------------------------
    def _histogram(
        self, rect: tuple[float, float, float, float], step: int = 2
    ) -> tuple[dict[tuple[int, int, int], int], int]:
        """Distribution des couleurs (quantifiées) sur une zone, et nombre d'échantillons."""
        left, top, right, bottom = self._to_pixels(rect)
        buckets: dict[tuple[int, int, int], int] = {}
        total = 0
        for py in range(top, bottom, step):
            for px in range(left, right, step):
                rgb = self._pixel(px, py)
                key = (
                    rgb[0] // COLOR_BUCKET * COLOR_BUCKET,
                    rgb[1] // COLOR_BUCKET * COLOR_BUCKET,
                    rgb[2] // COLOR_BUCKET * COLOR_BUCKET,
                )
                buckets[key] = buckets.get(key, 0) + 1
                total += 1
        return buckets, total

    def analyze(self, rect: tuple[float, float, float, float]) -> BlockColors:
        """Couleur dominante du fond et couleur d'encre du texte d'origine.

        La couleur **dominante** d'une zone élargie autour du bloc est le fond
        (une cellule est majoritairement de la couleur de son fond). L'encre est
        ensuite la couleur la plus **éloignée** de ce fond parmi les pixels du bloc :
        cela fonctionne aussi bien pour du texte noir sur fond blanc que pour du
        texte blanc sur un bandeau gris foncé.
        """
        x0, y0, x1, y1 = rect
        margin = BACKGROUND_MARGIN
        outer_buckets, outer_total = self._histogram(
            (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
        )
        if outer_total == 0:
            return BlockColors()

        background_key, background_count = max(outer_buckets.items(), key=lambda kv: kv[1])
        if background_count / outer_total < BACKGROUND_SHARE_MIN:
            # Fond non uniforme (photo, dégradé) : on ne masquera pas.
            return BlockColors(background=None, ink=None)

        # Couleur moyenne des pixels proches de la dominante (évite une couleur « en escalier »).
        background = _average_near(outer_buckets, background_key)

        # Encre : couleur du bloc la plus éloignée du fond. Le texte est fin, donc
        # on échantillonne au pixel près et on se contente de quelques occurrences
        # (mais on ignore les pixels isolés, souvent des artefacts de bordure).
        inner_buckets, inner_total = self._histogram((x0, y0, x1, y1), step=1)
        ink: tuple[int, int, int] | None = None
        best_distance = 0.0
        if inner_total:
            minimum = 2 if inner_total < 4000 else max(3, int(inner_total * 0.005))
            for key, count in inner_buckets.items():
                if count < minimum:
                    continue
                distance = _distance(key, background_key)
                if distance > best_distance:
                    best_distance, ink = distance, _average_near(inner_buckets, key)
        if best_distance < INK_DISTANCE_MIN:
            ink = None

        return BlockColors(background=_to_unit(background), ink=_to_unit(ink) if ink else None)


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def _average_near(
    buckets: dict[tuple[int, int, int], int], key: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Moyenne pondérée des couleurs proches de `key` (lissage du fond)."""
    red = green = blue = weights = 0
    for other, count in buckets.items():
        if _distance(other, key) <= COLOR_BUCKET * 2:
            red += other[0] * count
            green += other[1] * count
            blue += other[2] * count
            weights += count
    if not weights:
        return key
    return (red // weights, green // weights, blue // weights)


def analyze_pages(
    pdf_bytes: bytes, rects_per_page: list[list[tuple[float, float, float, float]]]
) -> list[list[BlockColors]]:
    """Analyse les blocs de chaque page (silencieux en cas d'échec : jamais bloquant)."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - déploiement sans PyMuPDF
        return []

    results: list[list[BlockColors]] = []
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # pragma: no cover - PDF exotique
        logger.info("Analyse des couleurs impossible (PDF illisible)", exc_info=True)
        return []

    try:
        for index, rects in enumerate(rects_per_page):
            if index >= document.page_count:
                results.append([])
                continue
            page = document[index]
            try:
                pixmap = page.get_pixmap(dpi=ANALYSIS_DPI, colorspace=pymupdf.csRGB)
            except Exception:  # pragma: no cover
                results.append([])
                continue
            analysis = PageAnalysis(pixmap, page.rect.width)
            results.append([analysis.analyze(rect) for rect in rects])
    finally:
        document.close()
    return results
