"""Catalog access for the artifact renderers (step 18).

The renderers are pure: the same run document, renderer version and
language give the same bytes. Language is therefore an explicit argument
that rides the rendering - never ambient browser state - and the catalog
it is resolved against is the compiled set the repository ships
(``translations/<lang>/LC_MESSAGES/messages.mo``).

Discipline:

* an unknown language is refused, never silently rendered as English
  (an explicit refusal is honest; a silent fallback is not);
* a missing *entry* falls back to the msgid and is counted - the count
  lands in the artifact's rendering notes, so "this file has untranslated
  chrome" is stated on the file's own manifest rather than hidden;
* ``local_digits`` is applied by the renderer only to its own pure
  chrome phrases (a row count, a limit) - never to a whole sentence with
  substitutions, because substituted values (keys, fingerprints, names)
  are data and are never re-digitised.
"""

from __future__ import annotations

import gettext as _gettext
from typing import Any, Dict, FrozenSet, Tuple

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRANSLATIONS_DIR = PROJECT_ROOT / "translations"

#: Languages the artifact renderers accept - the shipped Babel catalogs.
RTL_LANGUAGES: FrozenSet[str] = frozenset({"ar", "he", "fa"})

_DIGITS: Dict[str, Tuple[str, ...]] = {
    "ar": ("٠", "١", "٢", "٣", "٤", "٥", "٦", "٧", "٨", "٩"),
    "fa": ("۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"),
}


def available_languages() -> FrozenSet[str]:
    """The languages with a compiled catalog, discovered from disk."""
    found = set()
    try:
        for path in TRANSLATIONS_DIR.iterdir():
            if (path / "LC_MESSAGES" / "messages.mo").is_file():
                found.add(path.name)
    except OSError:
        pass
    return frozenset(found or {"en"})


def is_rtl(language: str) -> bool:
    return language in RTL_LANGUAGES


def local_digits(text: str, language: str) -> str:
    """ASCII digits in the renderer's own phrases, in the language's
    digits - identity for languages without a digit set declared."""
    mapping = _DIGITS.get(language)
    if not mapping:
        return text
    return "".join(mapping[int(ch)] if ch.isdigit() else ch for ch in text)


class Reader:
    """A language's catalog with an explicit, counted fallback."""

    def __init__(self, language: str) -> None:
        self.language = language
        self.fallbacks = 0
        self._t = _gettext.translation(
            "messages", localedir=str(TRANSLATIONS_DIR), languages=[language],
            fallback=True)

    def gettext(self, msgid: str, **kwargs: Any) -> str:
        text = self._t.gettext(msgid)
        if msgid and text == msgid and self.language != "en":
            # A genuine gap: the catalog shows the msgid. (The empty msgid
            # is the catalog header, never a gap; English's catalog entries
            # are empty by design: the source language IS the msgid.)
            self.fallbacks += 1
        if kwargs:
            text = text % kwargs   # gettext convention: named %(placeholder)s
        return text

    def ngettext(self, singular: str, plural: str, n: int, **kwargs: Any) -> str:
        text = self._t.ngettext(singular, plural, n)
        if text in (singular, plural) and self.language != "en":
            self.fallbacks += 1
        if kwargs:
            text = text % kwargs
        return text



def reader_for(language: Any) -> Reader:
    """The reader for ``language``; an unknown language is refused."""
    if language not in available_languages():
        raise ValueError(
            f"language must be one of: {', '.join(sorted(available_languages()))}")
    return Reader(language)
