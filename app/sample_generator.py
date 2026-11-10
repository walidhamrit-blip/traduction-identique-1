"""Générateur d'un PDF de démonstration riche (facture + rapport avec graphiques vectoriels)."""

from __future__ import annotations

import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def generate_sample_pdf() -> bytes:
    """Crée un document PDF A4 structuré (logo vectoriel, cadres, tableau, paragraphe)
    permettant de vérifier visuellement que les éléments graphiques sont préservés
    tandis que les textes sont masqués en blanc et traduits aux coordonnées exactes.
    """
    buf = io.BytesIO()
    width, height = A4  # 595.27 x 841.89 pt
    c = canvas.Canvas(buf, pagesize=A4)

    # --- Bandeau décoratif latéral & Logo vectoriel (hors zones texte) ---
    c.setFillColorRGB(0.09, 0.24, 0.48)
    c.rect(0, height - 10, width, 10, stroke=0, fill=1)

    # Icône géométrique de marque en haut à gauche
    c.setFillColorRGB(0.09, 0.24, 0.48)
    c.roundRect(45, height - 82, 42, 42, 8, stroke=0, fill=1)
    c.setStrokeColorRGB(1, 1, 1)
    c.setLineWidth(2.2)
    c.line(55, height - 68, 66, height - 50)
    c.line(66, height - 50, 77, height - 68)
    c.circle(66, height - 62, 11, stroke=1, fill=0)

    # --- En-tête du document ---
    c.setFillColorRGB(0.08, 0.10, 0.15)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(100, height - 58, "Commercial Invoice")

    c.setFont("Helvetica", 10.5)
    c.setFillColorRGB(0.30, 0.34, 0.40)
    c.drawString(100, height - 76, "Reference: INV-2026-089")

    # --- Cadres d'informations (2 colonnes) ---
    c.setStrokeColorRGB(0.82, 0.85, 0.90)
    c.setLineWidth(1)
    c.roundRect(45, height - 195, 240, 90, 6, stroke=1, fill=0)
    c.roundRect(310, height - 195, 240, 90, 6, stroke=1, fill=0)

    c.setFillColorRGB(0.08, 0.10, 0.15)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(58, height - 125, "Invoice Details")
    c.setFont("Helvetica", 10)
    c.drawString(58, height - 145, "Date: 2026-11-10")
    c.drawString(58, height - 163, "Status: Approved")
    c.drawString(58, height - 181, "Page: 1 / 1")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(323, height - 125, "Bill To")
    c.setFont("Helvetica", 10)
    c.drawString(323, height - 145, "Customer: Atlas Digital SARL")
    c.drawString(323, height - 163, "Company: Mediterranean Tech Group")
    c.drawString(323, height - 181, "Reference: PO-99412")

    # --- Section Résumé Exécutif (paragraphe multi-lignes) ---
    c.setStrokeColorRGB(0.16, 0.42, 0.76)
    c.setLineWidth(2.5)
    c.line(45, height - 220, 45, height - 285)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(58, height - 232, "Executive Summary")

    c.setFont("Helvetica", 10)
    c.drawString(
        58,
        height - 252,
        "This document certifies that the cloud architecture audit and automated",
    )
    c.drawString(
        58,
        height - 267,
        "PDF translation pipeline meet production quality standards.",
    )

    # --- Tableau des prestations (bordures vectorielles préservées) ---
    table_top = height - 315
    row_h = 30
    col_x = [45, 315, 390, 475, 550]

    # Lignes horizontales du tableau
    c.setStrokeColorRGB(0.75, 0.79, 0.85)
    c.setLineWidth(0.9)
    for r in range(5):
        y = table_top - r * row_h
        c.line(col_x[0], y, col_x[-1], y)

    # Lignes verticales du tableau
    for x in col_x:
        c.line(x, table_top, x, table_top - 4 * row_h)

    # En-têtes de colonnes
    c.setFont("Helvetica-Bold", 10)
    c.drawString(55, table_top - 19, "Description")
    c.drawString(325, table_top - 19, "Quantity")
    c.drawString(400, table_top - 19, "Unit Price")
    c.drawString(485, table_top - 19, "Amount")

    # Lignes du tableau
    rows = [
        ("Cloud Infrastructure & API Hosting", "1", "1,200.00 EUR", "1,200.00 EUR"),
        ("Multilingual Document Processing License", "2", "650.00 EUR", "1,300.00 EUR"),
        ("Technical Support & SLA 24/7", "1", "500.00 EUR", "500.00 EUR"),
    ]
    c.setFont("Helvetica", 9.5)
    for idx, (desc, qty, unit, amt) in enumerate(rows, start=1):
        y_text = table_top - idx * row_h - 19
        c.drawString(55, y_text, desc)
        c.drawString(325, y_text, qty)
        c.drawString(400, y_text, unit)
        c.drawString(485, y_text, amt)

    # --- Récapitulatif financier ---
    tot_top = table_top - 4 * row_h - 25
    c.roundRect(350, tot_top - 72, 200, 78, 6, stroke=1, fill=0)

    c.setFont("Helvetica", 10)
    c.drawString(362, tot_top - 16, "Subtotal")
    c.drawString(475, tot_top - 16, "3,000.00 EUR")

    c.drawString(362, tot_top - 36, "Tax (20%)")
    c.drawString(475, tot_top - 36, "600.00 EUR")

    c.line(358, tot_top - 46, 542, tot_top - 46)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(362, tot_top - 62, "Total Due")
    c.drawString(475, tot_top - 62, "3,600.00 EUR")

    # --- Pied de page ---
    c.setStrokeColorRGB(0.84, 0.87, 0.91)
    c.line(45, 95, width - 45, 95)

    c.setFont("Helvetica", 9.5)
    c.setFillColorRGB(0.25, 0.28, 0.33)
    c.drawString(45, 74, "Payment terms: Net 30 days via wire transfer.")
    c.drawString(45, 56, "Thank you for your business and continued trust.")

    c.showPage()
    c.save()
    return buf.getvalue()
