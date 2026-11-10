"""Fonction mock de traduction multilingue (en, fr, ar).

Conçue pour être remplacée de manière transparente par un connecteur externe
(DeepL, OpenAI, Google Cloud Translation, Azure AI Translator, etc.).
"""

import re
from app.schemas import TargetLang

# Dictionnaire de phrases complètes courantes (factures, contrats, rapports techniques)
_PHRASE_DICTIONARY: dict[str, dict[TargetLang, str]] = {
    "commercial invoice": {
        TargetLang.EN: "Commercial Invoice",
        TargetLang.FR: "Facture Commerciale",
        TargetLang.AR: "فاتورة تجارية",
    },
    "facture commerciale": {
        TargetLang.EN: "Commercial Invoice",
        TargetLang.FR: "Facture Commerciale",
        TargetLang.AR: "فاتورة تجارية",
    },
    "executive summary": {
        TargetLang.EN: "Executive Summary",
        TargetLang.FR: "Résumé Exécutif",
        TargetLang.AR: "الملخص التنفيذي",
    },
    "résumé exécutif": {
        TargetLang.EN: "Executive Summary",
        TargetLang.FR: "Résumé Exécutif",
        TargetLang.AR: "الملخص التنفيذي",
    },
    "invoice details": {
        TargetLang.EN: "Invoice Details",
        TargetLang.FR: "Détails de la facture",
        TargetLang.AR: "تفاصيل الفاتورة",
    },
    "bill to": {
        TargetLang.EN: "Bill To",
        TargetLang.FR: "Facturer à",
        TargetLang.AR: "فاتورة إلى",
    },
    "description": {
        TargetLang.EN: "Description",
        TargetLang.FR: "Désignation",
        TargetLang.AR: "الوصف",
    },
    "quantity": {
        TargetLang.EN: "Quantity",
        TargetLang.FR: "Quantité",
        TargetLang.AR: "الكمية",
    },
    "unit price": {
        TargetLang.EN: "Unit Price",
        TargetLang.FR: "Prix unitaire",
        TargetLang.AR: "سعر الوحدة",
    },
    "amount": {
        TargetLang.EN: "Amount",
        TargetLang.FR: "Montant",
        TargetLang.AR: "المبلغ",
    },
    "subtotal": {
        TargetLang.EN: "Subtotal",
        TargetLang.FR: "Sous-total",
        TargetLang.AR: "المجموع",
    },
    "tax (20%)": {
        TargetLang.EN: "Tax (20%)",
        TargetLang.FR: "TVA (20 %)",
        TargetLang.AR: "الضريبة (20%)",
    },
    "total due": {
        TargetLang.EN: "Total Due",
        TargetLang.FR: "Total TTC",
        TargetLang.AR: "الإجمالي",
    },
    "cloud infrastructure & api hosting": {
        TargetLang.EN: "Cloud Infrastructure & API Hosting",
        TargetLang.FR: "Infrastructure Cloud & Hébergement API",
        TargetLang.AR: "البنية التحتية السحابية واستضافة الواجهات",
    },
    "multilingual document processing license": {
        TargetLang.EN: "Multilingual Document Processing License",
        TargetLang.FR: "Licence de traitement documentaire multilingue",
        TargetLang.AR: "ترخيص معالجة المستندات متعددة اللغات",
    },
    "technical support & sla 24/7": {
        TargetLang.EN: "Technical Support & SLA 24/7",
        TargetLang.FR: "Support technique & garantie SLA 24/7",
        TargetLang.AR: "الدعم الفني وضمان الخدمة على مدار الساعة",
    },
    "payment terms: net 30 days via wire transfer.": {
        TargetLang.EN: "Payment terms: Net 30 days via wire transfer.",
        TargetLang.FR: "Conditions de règlement : 30 jours nets par virement bancaire.",
        TargetLang.AR: "شروط الدفع: صافي 30 يوما عبر التحويل البنكي.",
    },
    "thank you for your business and continued trust.": {
        TargetLang.EN: "Thank you for your business and continued trust.",
        TargetLang.FR: "Merci pour votre confiance et votre collaboration.",
        TargetLang.AR: "شكرا لتعاملكم معنا وثقتكم المستمرة.",
    },
    "this document certifies that the cloud architecture audit and automated pdf translation pipeline meet production quality standards.": {
        TargetLang.EN: (
            "This document certifies that the cloud architecture audit and automated "
            "PDF translation pipeline meet production quality standards."
        ),
        TargetLang.FR: (
            "Ce document certifie que l'audit d'architecture cloud et le pipeline "
            "automatisé de traduction PDF respectent les standards de production."
        ),
        TargetLang.AR: (
            "تشهد هذه الوثيقة بأن تدقيق البنية السحابية ونظام ترجمة ملفات PDF الآلي "
            "يلبيان معايير الجودة الإنتاجية المعتمدة."
        ),
    },
    "hello world": {
        TargetLang.EN: "Hello World",
        TargetLang.FR: "Bonjour le monde",
        TargetLang.AR: "مرحبا بالعالم",
    },
    "bonjour le monde": {
        TargetLang.EN: "Hello World",
        TargetLang.FR: "Bonjour le monde",
        TargetLang.AR: "مرحبا بالعالم",
    },
}

# Glossaire lexical mot-à-mot pour enrichir la traduction mock sur des textes libres
_WORD_GLOSSARY: dict[str, dict[TargetLang, str]] = {
    "invoice": {TargetLang.EN: "Invoice", TargetLang.FR: "Facture", TargetLang.AR: "فاتورة"},
    "facture": {TargetLang.EN: "Invoice", TargetLang.FR: "Facture", TargetLang.AR: "فاتورة"},
    "date": {TargetLang.EN: "Date", TargetLang.FR: "Date", TargetLang.AR: "التاريخ"},
    "client": {TargetLang.EN: "Customer", TargetLang.FR: "Client", TargetLang.AR: "العميل"},
    "customer": {TargetLang.EN: "Customer", TargetLang.FR: "Client", TargetLang.AR: "العميل"},
    "company": {TargetLang.EN: "Company", TargetLang.FR: "Société", TargetLang.AR: "الشركة"},
    "total": {TargetLang.EN: "Total", TargetLang.FR: "Total", TargetLang.AR: "الإجمالي"},
    "page": {TargetLang.EN: "Page", TargetLang.FR: "Page", TargetLang.AR: "صفحة"},
    "report": {TargetLang.EN: "Report", TargetLang.FR: "Rapport", TargetLang.AR: "تقرير"},
    "rapport": {TargetLang.EN: "Report", TargetLang.FR: "Rapport", TargetLang.AR: "تقرير"},
    "status": {TargetLang.EN: "Status", TargetLang.FR: "Statut", TargetLang.AR: "الحالة"},
    "paid": {TargetLang.EN: "Paid", TargetLang.FR: "Payée", TargetLang.AR: "مدفوعة"},
    "approved": {TargetLang.EN: "Approved", TargetLang.FR: "Approuvé", TargetLang.AR: "معتمد"},
    "reference": {TargetLang.EN: "Reference", TargetLang.FR: "Référence", TargetLang.AR: "المرجع"},
    "welcome": {TargetLang.EN: "Welcome", TargetLang.FR: "Bienvenue", TargetLang.AR: "مرحبا"},
    "document": {TargetLang.EN: "Document", TargetLang.FR: "Document", TargetLang.AR: "وثيقة"},
    "summary": {TargetLang.EN: "Summary", TargetLang.FR: "Résumé", TargetLang.AR: "ملخص"},
    "notes": {TargetLang.EN: "Notes", TargetLang.FR: "Remarques", TargetLang.AR: "ملاحظات"},
}

_NUMERIC_OR_SYMBOL_ONLY_RE = re.compile(
    r"^[\d\s.,:/+\-–—€$£%#()\[\]@]+(?:\s*(?:EUR|USD|GBP|CHF|MAD|TND|LYD|CAD))?$",
    re.IGNORECASE,
)
_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF]")


def mock_translate(text: str, target_lang: TargetLang | str) -> str:
    """Traduit (mock déterministe) un segment de texte vers `target_lang` ('en', 'fr', 'ar').

    Comportement :
    - Conserve intacts les blocs purement numériques, montants monétaires, dates ou codes.
    - Utilise un dictionnaire métier complet pour les phrases et libellés connus.
    - Applique un remplacement lexical cohérent ou génère une traduction mock adaptée
      (notamment en écriture arabe authentique pour 'ar') afin de démontrer le pipeline RTL.
    """
    lang = TargetLang(target_lang)
    normalized = " ".join(text.split())
    if not normalized:
        return ""

    # Les montants (ex: 1,200.00 EUR), numéros et dates purs restent intacts
    if _NUMERIC_OR_SYMBOL_ONLY_RE.match(normalized):
        return normalized

    lower_key = normalized.lower()
    if lower_key in _PHRASE_DICTIONARY:
        return _PHRASE_DICTIONARY[lower_key][lang]

    # Gestion des motifs "Clé: Valeur" (ex: "Reference: INV-2026-089", "Status: Approved")
    if ":" in normalized:
        left, right = normalized.split(":", 1)
        left_clean = left.strip().lower().rstrip("#").strip()
        right_clean = right.strip()
        if left_clean in _WORD_GLOSSARY:
            translated_key = _WORD_GLOSSARY[left_clean][lang]
            translated_val = _WORD_GLOSSARY.get(right_clean.lower(), {}).get(lang, right_clean)
            sep = " : " if lang == TargetLang.FR else ": "
            return f"{translated_key}{sep}{translated_val}"

    # Remplacement lexical mot-à-mot uniquement si tous les mots alphabétiques sont connus
    alpha_words = [w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]+", normalized)]
    if alpha_words and all(w in _WORD_GLOSSARY for w in alpha_words):
        tokens = re.split(r"(\W+)", normalized)
        return "".join(
            _WORD_GLOSSARY[t.lower()][lang] if t.lower() in _WORD_GLOSSARY else t
            for t in tokens
        )

    if lang == TargetLang.AR:
        if _ARABIC_CHAR_RE.search(normalized):
            return normalized
        numbers = " ".join(re.findall(r"\d+(?:[.,/-]\d+)*", normalized))
        suffix = f" {numbers}" if numbers else ""
        if len(normalized) <= 25:
            return f"نص مترجم{suffix}"
        return f"ترجمة آلية للنص الأصلي ضمن المستند{suffix}"

    prefix = "[EN]" if lang == TargetLang.EN else "[FR]"
    return f"{prefix} {normalized}"
