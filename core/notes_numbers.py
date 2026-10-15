"""
core/notes_numbers.py -- ONE normaliser for COMPARING titles and names: number words and numerals are the same thing.

'Seven' = '7' = 'Sju' ; 'Part II' = 'Pt. 2' = 'Part Two' ; 'Volume One' = 'Vol. 1' ; 'No. 1' = 'Number One' = '#1' ;
'Ten Years After' = '10 Years After' ; 'Maroon Five' = 'Maroon 5' ; 'first' = '1st' ; '&' = 'and' = 'og'.

compare_key(text)  -> the comparable form (lower case, no accents or punctuation, numbers as digits). For comparing ONLY: it is
                      never written into a note, a tag or a file name.
title_variants(t)  -> [t] plus the other spelling of its numbers ('Seven' -> ['Seven', '7'], '7' -> ['7', 'Seven']), for the
                      searches (a search for 'Seven' must also try '7').

Words: English, Norwegian (en/ett, to, tre, fire, fem, seks, syv/sju, atte, ni, ti ...) up to twenty, ordinals up to twentieth.
Roman numerals II..XX are numbers only as standalone tokens; the single letters I, V and X stay letters ('I', 'Malcolm X').
"""

from __future__ import annotations

import re
import unicodedata

_EN = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
_NUMBERS: dict[str, str] = {w: str(i) for i, w in enumerate(_EN, 1)}
_NUMBERS.update({
    # Norwegian (and Swedish / Danish where they differ); 'to' and 'en' are also everyday words of other languages, which is
    # harmless: both sides of a comparison are normalised the same way
    "en": "1", "ett": "1", "ei": "1", "to": "2", "tre": "3", "fire": "4", "fem": "5", "seks": "6", "syv": "7", "sju": "7",
    "atte": "8", "ni": "9", "ti": "10", "elleve": "11", "tolv": "12", "tretten": "13", "fjorten": "14", "femten": "15",
    "seksten": "16", "sytten": "17", "atten": "18", "nitten": "19", "tjue": "20", "tjuge": "20",
})
_NUMBERS["fjorton"] = "14"
_ROMAN = {r: str(i) for i, r in enumerate(
    "i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx".split(), 1)}
_ROMAN_LETTERS = {"i", "v", "x"}  # alone these stay letters
_ORD_WORDS = ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth "
              "sixteenth seventeenth eighteenth nineteenth twentieth").split()


def _suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


_ORDINALS = {w: f"{i}{_suffix(i)}" for i, w in enumerate(_ORD_WORDS, 1)}
_SAME = {"og": "and", "und": "and", "och": "and", "pt": "part", "volume": "vol", "volum": "vol", "nr": "no", "nummer": "no",
         "number": "no"}
_DIGITS = re.compile(r"^\d+$")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold().replace("&", " and ")
    return re.sub(r"[\W_]+", " ", text).strip()  # letters and digits of any script stay


def compare_key(text: str) -> str:
    """The comparable form of a title or a name: folded, number words / roman numerals as digits, 'Pt.' as 'Part',
    'Volume' as 'Vol', 'Number' / 'Nr' as 'No', 'og' / 'und' as 'and'."""
    tokens = _fold(text).split()
    out: list[str] = []
    for token in tokens:
        if token in _ORDINALS:
            token = _ORDINALS[token]
        elif token in _NUMBERS:
            token = _NUMBERS[token]
        elif token in _ROMAN and token not in _ROMAN_LETTERS:
            token = _ROMAN[token]
        else:
            token = _SAME.get(token, token)
        out.append(token)
    # 'No. 1' / 'Number One' / '#1' are one thing: the label 'no' counts only in front of a number ('No Doubt' stays)
    out = [t for i, t in enumerate(out) if not (t == "no" and i + 1 < len(out) and _DIGITS.match(out[i + 1]))]
    return " ".join(out)


def _digit_to_word(n: str) -> str:
    i = int(n)
    return _EN[i - 1].capitalize() if 1 <= i <= len(_EN) else ""


def title_variants(title: str) -> list[str]:
    """`title` and, when it has a number as a word (or as digits), the same title with that number spelled the other way:
    'Seven' -> ['Seven', '7'], '7' -> ['7', 'Seven'], 'Part Two' -> ['Part Two', 'Part 2'], 'Ten Years After' -> [..., '10 Years After'].
    Only the first number in the title is converted, and only English words (the search service is asked in the form a
    catalogue would carry)."""
    title = (title or "").strip()
    variants = [title] if title else []
    if not title:
        return variants
    parts = re.split(r"(\W+)", title)
    for i, part in enumerate(parts):
        low = part.casefold()
        replacement = ""
        if low in _NUMBERS and low in _EN:
            replacement = _NUMBERS[low]
        elif low in _NUMBERS:  # a Norwegian word: the digit form
            replacement = _NUMBERS[low]
        elif _DIGITS.match(part) and 1 <= int(part) <= len(_EN):
            replacement = _digit_to_word(part)
        elif low in _ROMAN and low not in _ROMAN_LETTERS:
            replacement = _ROMAN[low]
        if replacement:
            alt = "".join(parts[:i]) + replacement + "".join(parts[i + 1:])
            if alt.casefold() != title.casefold():
                variants.append(alt)
            break
    return variants
