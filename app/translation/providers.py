"""Fournisseurs de traduction **gratuits**, sans clé API.

Chaque fournisseur expose la même interface :

    translate_batch(texts: list[str], source: str, target: str) -> list[str]

et lève `ProviderUnavailable` en cas d'échec (réseau, quota, format), ce qui
permet à la chaîne de passer automatiquement au suivant.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 12  # secondes par requête (Vercel : 60 s max par invocation)


class ProviderUnavailable(RuntimeError):
    """Le fournisseur ne peut pas répondre (réseau, quota, réponse invalide)."""


class TranslationProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def translate_batch(
        self, texts: list[str], source: str = "auto", target: str = "ar"
    ) -> list[str]:
        """Traduit une liste de segments et retourne une liste de même longueur."""

    @staticmethod
    def _http(
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> bytes:
        merged = {"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"}
        if headers:
            merged.update(headers)
        request = Request(url, data=data, headers=merged, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            raise ProviderUnavailable(f"HTTP {exc.code} de {url[:80]}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(f"Réseau : {exc}") from exc


class GoogleWebProvider(TranslationProvider):
    """Point d'entrée public Google Traduction (`client=gtx`).

    Gratuit et sans clé. À utiliser avec des volumes raisonnables : l'appel
    n'est pas contractuel et peut être limité ; la chaîne bascule alors sur
    le fournisseur suivant.
    """

    name = "google_web"
    ENDPOINT = "https://translate.googleapis.com/translate_a/single"
    MAX_BATCH_CHARS = 4000
    MAX_SEGMENTS = 40

    def translate_batch(
        self, texts: list[str], source: str = "auto", target: str = "ar"
    ) -> list[str]:
        results: list[str] = []
        for chunk in self._chunk(texts):
            query = urlencode(
                {
                    "client": "gtx",
                    "sl": source or "auto",
                    "tl": target,
                    "dt": "t",
                    "q": "\n".join(chunk),
                }
            )
            raw = self._http(f"{self.ENDPOINT}?{query}")
            results.extend(self._parse(raw, expected=len(chunk)))
        return results

    def _chunk(self, texts: list[str]) -> list[list[str]]:
        chunks: list[list[str]] = []
        current: list[str] = []
        size = 0
        for text in texts:
            if current and (size + len(text) > self.MAX_BATCH_CHARS or len(current) >= self.MAX_SEGMENTS):
                chunks.append(current)
                current, size = [], 0
            current.append(text)
            size += len(text) + 1
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _parse(raw: bytes, expected: int) -> list[str]:
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
            pieces = [part[0] for part in payload[0] if part and part[0]]
            translated = "".join(pieces)
        except (ValueError, TypeError, IndexError, KeyError) as exc:
            raise ProviderUnavailable(f"Réponse Google illisible : {exc}") from exc

        lines = [line for line in translated.split("\n")]
        # Google peut fusionner ou scinder des lignes : on réaligne prudemment.
        if len(lines) == expected:
            return [line.strip() for line in lines]
        if expected == 1:
            return [translated.strip()]
        raise ProviderUnavailable(
            f"Désalignement de la réponse ({len(lines)} lignes reçues pour {expected} envoyées)"
        )


class MyMemoryProvider(TranslationProvider):
    """API gratuite sans clé (quota quotidien par IP, ~5 000 caractères/jour)."""

    name = "mymemory"
    ENDPOINT = "https://api.mymemory.translated.net/get"
    MAX_QUERY_CHARS = 480

    def translate_batch(
        self, texts: list[str], source: str = "auto", target: str = "ar"
    ) -> list[str]:
        src = "en" if source in ("auto", "") else source
        out: list[str] = []
        for text in texts:
            if len(text) > self.MAX_QUERY_CHARS:
                raise ProviderUnavailable("Segment trop long pour MyMemory")
            query = urlencode({"q": text, "langpair": f"{src}|{target}"})
            raw = self._http(f"{self.ENDPOINT}?{query}")
            try:
                payload = json.loads(raw.decode("utf-8", "replace"))
                translated = payload["responseData"]["translatedText"]
                status = int(payload.get("responseStatus", 200) or 200)
            except (ValueError, TypeError, KeyError) as exc:
                raise ProviderUnavailable(f"Réponse MyMemory illisible : {exc}") from exc
            if status >= 400 or "MYMEMORY WARNING" in str(translated).upper():
                raise ProviderUnavailable("Quota MyMemory atteint")
            out.append(str(translated).strip())
            time.sleep(0.05)
        return out


class LibreTranslateProvider(TranslationProvider):
    """LibreTranslate (gratuit, open source, instance publique ou auto-hébergée)."""

    name = "libretranslate"
    MAX_BATCH_CHARS = 2500

    def __init__(self, base_url: str = "https://libretranslate.com", api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def translate_batch(
        self, texts: list[str], source: str = "auto", target: str = "ar"
    ) -> list[str]:
        payload: dict[str, object] = {
            "q": texts,
            "source": "auto" if source in ("auto", "") else source,
            "target": target,
            "format": "text",
        }
        if self.api_key:
            payload["api_key"] = self.api_key
        raw = self._http(
            f"{self.base_url}/translate",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            decoded = json.loads(raw.decode("utf-8", "replace"))
            translated = decoded["translatedText"]
        except (ValueError, TypeError, KeyError) as exc:
            raise ProviderUnavailable(f"Réponse LibreTranslate illisible : {exc}") from exc
        if isinstance(translated, str):
            translated = [translated]
        if len(translated) != len(texts):
            raise ProviderUnavailable("Désalignement LibreTranslate")
        return [str(item).strip() for item in translated]


class OfflineGlossaryProvider(TranslationProvider):
    """Dernier recours : glossaire local, sans réseau.

    Ne fabrique **jamais** de faux texte : un segment inconnu est renvoyé tel
    quel, ce qui reste lisible et n'abîme pas le document.
    """

    name = "offline"

    def __init__(self) -> None:
        from app.translator import mock_translate

        self._mock = mock_translate

    def translate_batch(
        self, texts: list[str], source: str = "auto", target: str = "ar"
    ) -> list[str]:
        from app.translator import _PHRASE_DICTIONARY, _WORD_GLOSSARY  # noqa: PLC0415
        from app.schemas import TargetLang  # noqa: PLC0415

        try:
            lang = TargetLang(target)
        except ValueError:
            return list(texts)

        known: dict[str, str] = {}
        known.update({k: v[lang] for k, v in _PHRASE_DICTIONARY.items()})
        known.update({k: v[lang] for k, v in _WORD_GLOSSARY.items()})

        out: list[str] = []
        for text in texts:
            cleaned = " ".join(text.split())
            hit = known.get(cleaned.lower())
            out.append(hit if hit else cleaned)
        return out
