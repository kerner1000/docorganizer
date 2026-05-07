"""ASCII-only path enforcement and language-aware transliteration.

Decision: ADR-0009. Filenames and folder names are pure ASCII. The
``filename_charset`` block in ``docorganizer.yaml`` declares an ordered
character map (German ß→ss, ä→ae, …) and a fallback that decomposes any
remaining diacritics via NFD and drops combining marks (Latvian, Czech,
Polish, …). A final ASCII guard rejects whatever still doesn't fit.
"""

import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FilenameCharset:
    """Charset enforcement policy applied to filenames and folder segments."""

    transliterate: dict[str, str] = field(default_factory=dict)
    strip_remaining_diacritics: bool = True
    enforce_ascii: bool = True

    @classmethod
    def default(cls) -> "FilenameCharset":
        """Sensible default — German transliteration + diacritic strip + ASCII guard.

        Used when ``docorganizer.yaml`` omits the ``filename_charset`` block
        so the rule is on by default; archives that want the old behavior
        must opt out explicitly.
        """
        return cls(
            transliterate={
                "ß": "ss",
                "ä": "ae", "ö": "oe", "ü": "ue",
                "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
            },
            strip_remaining_diacritics=True,
            enforce_ascii=True,
        )


def to_ascii(text: str, charset: FilenameCharset) -> str:
    """Apply the charset policy to ``text`` and return the ASCII form.

    Order: explicit transliteration map → NFD decomposition (drop combining
    marks) → final guard. Raises ``ValueError`` if ``enforce_ascii`` is set
    and the result still has non-ASCII bytes.
    """
    if not text:
        return text

    out = text
    for src, dst in charset.transliterate.items():
        if src in out:
            out = out.replace(src, dst)

    if charset.strip_remaining_diacritics:
        decomposed = unicodedata.normalize("NFD", out)
        out = "".join(c for c in decomposed if not unicodedata.combining(c))

    if charset.enforce_ascii and not out.isascii():
        non_ascii = sorted({c for c in out if ord(c) > 127})
        raise ValueError(
            f"Non-ASCII characters remain after transliteration: {non_ascii!r}. "
            f"Add explicit map entries to docorganizer.yaml > filename_charset.transliterate "
            f"(input: {text!r})"
        )
    return out


def path_to_ascii(path: str, charset: FilenameCharset) -> str:
    """Transliterate every segment of a slash-separated relative path.

    Empty segments and the absolute-path leading slash are preserved so a
    plain rejoin reproduces the original structure with each segment
    transliterated independently.
    """
    if not path:
        return path
    sep = "/"
    return sep.join(to_ascii(part, charset) for part in path.split(sep))


def is_ascii(text: str) -> bool:
    """Return True if ``text`` is empty or contains only ASCII bytes."""
    return text.isascii() if text else True
