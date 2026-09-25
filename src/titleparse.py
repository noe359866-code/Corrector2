"""Nombre de release -> {título, año, temporada, episodio, ...}.

Ejemplos:
    The.Matrix.1999.1080p.BluRay.x264-AMIABLE -> The Matrix (1999)
    [SubsPlease] Jujutsu Kaisen - 24 (1080p)  -> Jujutsu Kaisen, absolute=24
    Dune.Part.Two.2024.2160p...               -> Dune Part Two (2024)
"""
from __future__ import annotations

import re

_QUALITY = re.compile(r"\b(2160p|1440p|1080p|720p|480p|360p|240p|4k|uhd)\b", re.I)
_CODEC = re.compile(r"\b(x264|x265|h\.?264|h\.?265|hevc|xvid|av1|vp9|aac|ac3|dts)\b", re.I)
_ACODEC = re.compile("\\b(ddp?5.1|dd5.1|dts.hd|truehd|eac3|flac|atmos)\\b", re.I)
_HDR = re.compile(r"\b(hdr10|hdr|dolby.?vision|\bdv\b|hlg|sdr)\b", re.I)
_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_SE = re.compile(r"\b[Ss](\d{1,2})[ ._-]*[Ee](\d{1,3})\b")          # S01E02
_X = re.compile(r"\b(\d{1,2})x(\d{1,3})\b")                          # 1x02
_SEASON_WORD = re.compile(r"\b[Ss]eason\.?\s*0?(\d{1,2})\b|\b[Tt]emporada\.?\s*0?(\d{1,2})\b")
_EP_WORD = re.compile(r"\b(?:[Ee]p?(?:isode)?|[Cc]ap(?:itulo|ítulo)?\.?)\s*0?(\d{1,3})\b")
_ANIME_DASH = re.compile(r"[-–]\s*0?(\d{1,3})\s*[\(\[]")              # ' - 24 (1080p)'
_SOURCE = re.compile(r"\b(web[ .-]?dl|webrip|bluray|bdrip|brrip|hdtv|dvdrip|dvd|vhs)\b", re.I)
_AUDIO_TAG = re.compile(r"\b(latino|castellano|dual|subs?|subtitulad[oa]|vose?)\b", re.I)
_GROUP_PRE = re.compile(r"^\s*[\(\[]([^()\[\]]{1,32})[\)\]]\s*")     # [SubsPlease] ...
_GROUP_SUF = re.compile(r"[-_]([A-Za-z0-9]{2,20})\s*$")              # ...-AMIABLE
_EXT = re.compile(r"\.(mkv|mp4|avi|m4v|ts|m2ts|torrent|nzb)$", re.I)
_BRACKETS = re.compile(r"[\[\](){}]")


def parse_release(name: str) -> dict:
    """Trocea un nombre de release. Nunca lanza: a malas devuelve el título."""
    original = (name or "").strip()
    work = _EXT.sub("", original).strip()

    quality = _first(_QUALITY, work)
    codec = _first(_CODEC, work)
    hdr = _first(_HDR, work)
    year = _year(work)
    season: int | None = None
    episode: int | None = None
    absolute: int | None = None

    m = _SE.search(work)
    if m:
        season, episode = int(m.group(1)), int(m.group(2))
    else:
        m = _X.search(work)
        if m and int(m.group(1)) <= 60:
            season, episode = int(m.group(1)), int(m.group(2))
        else:
            m = _SEASON_WORD.search(work)
            if m:
                season = int(m.group(1) or m.group(2))
            m = _EP_WORD.search(work)
            if m:
                num = int(m.group(1))
                if season is None and num <= 300:
                    absolute = num
                else:
                    episode = num
            elif season is None:
                m = _ANIME_DASH.search(work)
                if m and int(m.group(1)) <= 300:
                    absolute = int(m.group(1))

    group: str | None = None
    m = _GROUP_PRE.match(work)
    if m:
        group = m.group(1).strip() or None
    else:
        m = _GROUP_SUF.search(work)
        if m:
            group = m.group(1).strip() or None

    # --- limpiar el título ---
    title = work
    for rx in (_QUALITY, _CODEC, _ACODEC, _HDR, _SE, _X, _SEASON_WORD, _EP_WORD,
               _ANIME_DASH, _SOURCE, _AUDIO_TAG):
        title = rx.sub(" ", title)
    if year:
        title = re.sub(r"\b%d\b" % year, " ", title)
    title = _GROUP_PRE.sub(" ", title)
    title = _GROUP_SUF.sub(" ", title)
    title = _BRACKETS.sub(" ", title)
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r"\s*-\s*", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -")
    if not title:
        title = re.sub(r"\s+", " ", original).strip() or original

    return {
        "title": title,
        "year": year,
        "season": season,
        "episode": episode,
        "absolute": absolute,
        "quality": quality.lower() if quality else None,
        "codec": codec.lower() if codec else None,
        "hdr": hdr.lower() if hdr else None,
        "group": group,
    }


def _first(rx: re.Pattern, s: str) -> str | None:
    m = rx.search(s)
    return m.group(1) if m else None


def _year(s: str) -> int | None:
    for m in _YEAR.finditer(s):
        y = int(m.group(1))
        if 1900 <= y <= 2100:
            return y
    return None


# ---------------------------------------------------------------------------
# ¿Cuál de los dos nombres parece un release? (title vs title_text)
# ---------------------------------------------------------------------------
def release_signals(name: str) -> int:
    """Cuenta marcas típicas de release (calidad, códec, S01E02, año...)."""
    if not name:
        return 0
    s = str(name)
    n = 0
    if _QUALITY.search(s):
        n += 1
    if _CODEC.search(s):
        n += 1
    if _HDR.search(s):
        n += 1
    if _SE.search(s) or _X.search(s) or _EP_WORD.search(s) or _ANIME_DASH.search(s):
        n += 1
    if _YEAR.search(s):
        n += 1
    if _SOURCE.search(s):
        n += 1
    if s.count(".") >= 4:
        n += 1
    if "[" in s or "(" in s:
        n += 1
    if _GROUP_SUF.search(_EXT.sub("", s)):
        n += 1
    return n


def looks_like_release_name(name: str) -> bool:
    """True si parece un nombre de release (2+ marcas)."""
    return release_signals(name) >= 2


def pick_release_name(title: str | None, title_alt: str | None) -> tuple[str | None, str | None]:
    """Devuelve (principal, respaldo): el que más parece release va primero."""
    a = (title or "").strip() or None
    b = (title_alt or "").strip() or None
    if a and b and release_signals(b) > release_signals(a):
        return b, a
    return a, b
