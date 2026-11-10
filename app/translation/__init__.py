"""Chaîne de traduction **gratuite et sans clé API**.

Fournisseurs, du moins cher au plus cher (aucun n'exige de carte bancaire) :

1. ``google_web``   — point d'entrée public `translate_a/single` (gratuit, sans clé).
2. ``mymemory``     — API gratuite sans clé (quota quotidien par IP).
3. ``libretranslate`` — instance auto-hébergée ou publique (URL configurable).
4. ``offline``      — glossaire local, aucune requête réseau (mode dégradé honnête :
   le texte non reconnu est laissé tel quel, jamais remplacé par du faux texte).

La sélection se fait par la variable d'environnement ``TRANSLATOR_PROVIDER``
(``auto`` par défaut = essaie la chaîne dans l'ordre).
"""
