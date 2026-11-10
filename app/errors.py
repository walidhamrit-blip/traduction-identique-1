"""Erreurs métier typées du microservice.

Chaque erreur porte un `status_code` HTTP et un `code` stable, ce qui permet
à l'API de renvoyer une réponse JSON exploitable (au lieu d'un « HTTP 500 »
opaque) et au front d'afficher un message actionnable.
"""

from __future__ import annotations


class PdfServiceError(Exception):
    """Erreur applicative connue, traduite en réponse JSON structurée."""

    status_code: int = 500
    code: str = "internal_error"
    default_hint: str | None = None

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint or self.default_hint
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return payload


class InvalidPdfError(PdfServiceError):
    """Le fichier n'est pas un PDF exploitable (corrompu, tronqué, non-PDF)."""

    status_code = 400
    code = "invalid_pdf"
    default_hint = (
        "Vérifiez que le fichier est un PDF valide et non protégé par mot de passe."
    )


class EncryptedPdfError(PdfServiceError):
    """PDF chiffré / protégé par mot de passe : pdfminer et pypdf ne peuvent pas l'ouvrir."""

    status_code = 400
    code = "encrypted_pdf"
    default_hint = (
        "Retirez la protection par mot de passe du PDF (impression / export "
        "depuis un lecteur PDF) puis réessayez."
    )


class EmptyPdfError(PdfServiceError):
    """Aucune page ou aucun contenu exploitable."""

    status_code = 400
    code = "empty_pdf"


class PayloadTooLargeError(PdfServiceError):
    """Fichier trop volumineux pour la limite de la plateforme / du service."""

    status_code = 413
    code = "payload_too_large"
    default_hint = (
        "Sur Vercel, le corps de requête est limité à 4,5 Mo : découpez le PDF "
        "ou augmentez PDF_MAX_UPLOAD_MB si vous hébergez ailleurs."
    )


class TooManyPagesError(PdfServiceError):
    """Trop de pages : le traitement dépasserait la durée/largeur mémoire allouée."""

    status_code = 413
    code = "too_many_pages"
    default_hint = (
        "Découpez le PDF en plusieurs fichiers plus petits ou augmentez "
        "PDF_MAX_PAGES (et maxDuration) si votre hébergement le permet."
    )


class FontUnavailableError(PdfServiceError):
    """Polices TTF absentes du bundle déployé (cause classique de 500 sur Vercel)."""

    status_code = 503
    code = "font_unavailable"
    default_hint = (
        "Vérifiez que 'assets/fonts/Amiri-Regular.ttf' est bien déployé : le "
        "champ 'includeFiles' de vercel.json doit contenir 'assets/**'. "
        "Consultez /api/diag pour la liste des chemins inspectés."
    )


class PdfProcessingError(PdfServiceError):
    """Le traitement a échoué (PDF exotique, police illisible, PDF chiffré RCA…)."""

    status_code = 422
    code = "pdf_processing_failed"
    default_hint = (
        "Le PDF contient probablement une structure non prise en charge. "
        "Consultez /api/diag et les logs de la fonction pour le détail."
    )
