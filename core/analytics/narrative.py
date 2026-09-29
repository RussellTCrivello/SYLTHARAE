"""Deterministic, versioned narrative templates (five voices).

Every analytical section speaks in exactly five voices, in this order:

1. **Measure** - what was computed, over what.
2. **Finding** - what the numbers show.
3. **Confidence** - how far the finding can be trusted, citing the
   threshold (and its source) that was applied.
4. **Consequence** - what the finding implies for the reader's work, and
   what it does not imply.
5. **Caveat** - limits of the measure and the next step.

A template set is reviewed code: fixed msgids with ``%(name)s`` placeholders,
under a set id and version. An analysis stores which sentence it chose for
each voice (key + msgid) and the parameters, never prose, so the text is
reproducible from the stored record and renders in any supported language
through the translation catalogs. There is no free text generation anywhere:
the only variable content is the named parameters, and they are values the
analysis measured.

Changing a msgid or the choice rules changes what readers are told; bump the
set version (the version is in each analysis fingerprint and is recorded with
every stored narrative).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from core.criteria.model import sha256_hex

VOICES: Tuple[str, ...] = ("measure", "finding", "confidence", "consequence", "caveat")

_PLACEHOLDER = re.compile(r"%\((\w+)\)s")


class NarrativeError(ValueError):
    pass


def placeholders(msgid: str) -> Tuple[str, ...]:
    return tuple(sorted(set(_PLACEHOLDER.findall(msgid))))


@dataclass(frozen=True)
class TemplateSet:
    set_id: str
    version: int
    #: sentence key -> msgid. Keys are "<voice>.<variant>".
    sentences: Mapping[str, str]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.set_id or ""):
            raise NarrativeError(f"template set id {self.set_id!r} must be snake_case")
        if not isinstance(self.version, int) or self.version < 1:
            raise NarrativeError(f"{self.set_id}: version must be an integer >= 1")
        voices_seen = set()
        for key, msgid in self.sentences.items():
            voice, _, variant = key.partition(".")
            if voice not in VOICES or not re.fullmatch(r"[a-z][a-z0-9_]*", variant):
                raise NarrativeError(f"{self.set_id}: sentence key {key!r} must be "
                                     f"'<voice>.<variant>' with a voice in {VOICES}")
            if not isinstance(msgid, str) or msgid.strip() != msgid or not msgid:
                raise NarrativeError(f"{self.set_id}: {key} needs a trimmed msgid")
            if "%" in _PLACEHOLDER.sub("", msgid).replace("%%", ""):
                raise NarrativeError(f"{self.set_id}: {key} has a stray '%'")
            voices_seen.add(voice)
        missing = [v for v in VOICES if v not in voices_seen]
        if missing:
            raise NarrativeError(f"{self.set_id}: no sentence for voice(s) {missing}")

    @property
    def key(self) -> str:
        return f"{self.set_id}@{self.version}"

    def fingerprint(self) -> str:
        return sha256_hex({"set_id": self.set_id, "version": self.version,
                           "sentences": dict(sorted(self.sentences.items()))})

    def msgids(self) -> Tuple[str, ...]:
        return tuple(self.sentences[k] for k in sorted(self.sentences))

    def compose(self, choices: Sequence[Tuple[str, Mapping[str, Any]]]) -> Dict[str, Any]:
        """Stored narrative for ``choices`` = one ``(sentence_key, params)``
        per voice, in voice order. Every placeholder must be given, and no
        extra parameter is accepted (an unused value would be invisible)."""
        if [key.partition(".")[0] for key, _ in choices] != list(VOICES):
            raise NarrativeError(f"{self.set_id}: one sentence per voice, in order {VOICES}")
        out: List[Dict[str, Any]] = []
        for key, params in choices:
            if key not in self.sentences:
                raise NarrativeError(f"{self.set_id}: unknown sentence {key!r}")
            msgid = self.sentences[key]
            given = tuple(sorted(params))
            if given != placeholders(msgid):
                raise NarrativeError(f"{self.set_id}: {key} takes {placeholders(msgid)}, "
                                     f"got {given}")
            for name, value in params.items():
                if not (value is None or isinstance(value, (str, int, float))) \
                        or isinstance(value, bool):
                    raise NarrativeError(f"{self.set_id}: {key}.{name} must be text or a number")
            out.append({"voice": key.partition(".")[0], "key": key, "msgid": msgid,
                        "params": dict(params)})
        return {"template_set": self.set_id, "template_version": self.version,
                "template_fingerprint": self.fingerprint(), "voices": out}


def render(narrative: Mapping[str, Any], gettext: Callable[[str], str],
           format_number: Callable[[Any], str] = None) -> List[Dict[str, str]]:
    """Text of a stored narrative in the caller's language: each msgid is
    translated, then filled with its parameters (numbers through
    ``format_number``, so digits follow the locale). Returns
    ``[{"voice", "text"}]`` in voice order."""
    fmt = format_number or (lambda v: f"{v:,}" if isinstance(v, int) else
                            (f"{v:,.2f}" if isinstance(v, float) else str(v)))
    lines = []
    for voice in narrative["voices"]:
        params = {k: (fmt(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
                      else ("" if v is None else str(v)))
                  for k, v in voice["params"].items()}
        lines.append({"voice": voice["voice"], "text": gettext(voice["msgid"]) % params})
    return lines
