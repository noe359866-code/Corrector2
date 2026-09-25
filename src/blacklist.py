"""Tokens adulto + allowlist (misma lógica que sql/003_purge.sql).

Reglas:
  1) Frase con límite de palabra en título+grupo+tracker (~50 tokens).
  2) Marca pegada (substring) SOLO en grupo/tracker (xXxTorrents...).
  3) La allowlist siempre gana (xXx 2002, Adult Swim...).
"""
from __future__ import annotations

import re

from .textnorm import normalize, token_ok

# Misma lista que c_core en sql/003_purge.sql (mantener sincronizadas).
BLOCKED_CORE: tuple[str, ...] = (
    "xxx", "onlyfans", "porn", "porno", "hentai", "jav", "brazzers",
    "chaturbate", "creampie", "milf", "nsfw", "xvideos", "xvideo", "xnxx",
    "xhamster", "pornhub", "redtube", "youporn", "ahegao", "bukkake",
    "gangbang", "orgy", "bdsm", "fetish", "escort", "camgirl", "stripchat",
    "livejasmin", "myfreecams", "fansly", "manyvids", "clips4sale",
    "naughtyamerica", "realitykings", "bangbros", "mofos", "teamskeet",
    "blacked", "tushy", "vixen", "anal", "blowjob", "handjob", "cumshot",
    "stepmom", "stepsis", "taboo", "incest", "adult", "sex", "18+",
)

# Purga agresiva extra (opt-in: PURGE_SOFT_ADULT=true).
SOFT_ADULT: tuple[str, ...] = (
    "nude", "erotic", "erotica", "playboy", "webcam", "swinger", "swingers",
    "softcore", "pinup", "boudoir", "glamour", "sensual",
)

# Nunca se borran (misma que c_allow en sql/003_purge.sql).
ALLOW: tuple[str, ...] = (
    "xxx 2002", "xxx 2005", "xxx 2017", "xander cage", "state of the union",
    "adult swim", "sex and the city", "sex education",
    "analytics", "the analytics of love",
)

_SPECIAL = re.compile(r"[^a-zA-Z0-9 ]")


def default_tokens(soft: bool = False) -> list[str]:
    """Tokens para pasar a purge_blocked_torrents (p_soft_tokens)."""
    return list(SOFT_ADULT) if soft else []


def is_blocked(title: str | None, group: str | None = "",
               tracker: str | None = "",
               extra_blocked: tuple[str, ...] | list[str] = (),
               extra_allow: tuple[str, ...] | list[str] = (),
               soft: bool = False) -> tuple[bool, str]:
    """(bloqueado, motivo). Motivos: 'allow:...' | 'blocked:...' | 'soft:...'."""
    title = title or ""
    group = group or ""
    tracker = tracker or ""
    combined = f"{title} {group} {tracker}"
    norm_title = normalize(title)
    norm_gt = normalize(f"{group} {tracker}").replace(" ", "")

    for phrase in (*ALLOW, *extra_allow):
        p = normalize(phrase)
        if p and p in norm_title:
            return False, f"allow:{phrase}"

    tokens = [(*BLOCKED_CORE, *extra_blocked, *(SOFT_ADULT if soft else ()))]
    for tok in tokens[0]:
        tok = (tok or "").strip()
        if not tok:
            continue
        kind = "soft" if tok in SOFT_ADULT else "blocked"
        if _SPECIAL.search(tok):
            # token raro ('18+'): substring sobre el texto crudo
            if tok.lower() in combined.lower():
                return True, f"{kind}:{tok}"
            continue
        if token_ok(combined, tok):
            return True, f"{kind}:{tok}"
        nt = normalize(tok).replace(" ", "")
        if nt and norm_gt and nt in norm_gt:
            return True, f"{kind}:{tok} (grupo/tracker)"
    return False, ""
