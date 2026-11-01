"""日本語の表記ゆれを比べるための文字の正規化"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

try:
    import pykakasi  # 漢字の読みを調べるライブラリ

    _KAKASI = pykakasi.kakasi()
except Exception:  # ライブラリが無くても、読みの比較以外は動くようにする
    _KAKASI = None

_SEPARATORS = re.compile(r"[\s\-_・･.。、,，]+")


def kakasi_available() -> bool:
    return _KAKASI is not None


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def kata_to_hira(s: str) -> str:
    out = []
    for ch in s:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6 or code in (0x30FD, 0x30FE):
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def surface_key(text: str) -> str:
    """全角／半角・大文字／小文字・記号・ひらがな／カタカナ の違いを無視した比較用の文字列"""
    s = unicodedata.normalize("NFKC", text).lower()
    parts = [_SEPARATORS.sub("", p) for p in s.split("/")]
    return "/".join(kata_to_hira(p) for p in parts)


@lru_cache(maxsize=None)
def _reading(segment: str) -> str:
    if _KAKASI is None:
        return segment
    return "".join(item["hira"] for item in _KAKASI.convert(segment))


def reading_key(text: str) -> str:
    """漢字を読み（ひらがな）に直し、長音「ー」も無視した比較用の文字列"""
    parts = surface_key(text).split("/")
    return "/".join(_reading(p).replace("ー", "") for p in parts)


def has_kanji(s: str) -> bool:
    return any("一" <= ch <= "鿿" or "㐀" <= ch <= "䶿" for ch in s)
