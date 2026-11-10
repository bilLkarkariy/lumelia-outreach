"""Shared outbound copy guard, checked again after personalisation and at transport."""

import re
import unicodedata

REASON = "Les prix et tarifs ne doivent pas figurer dans les emails de prospection."
PRICE = re.compile(
    r"[€$£¥]|\b(?:eur|euros?|usd|dollars?|gbp|prix|tarifs?|pricing)\b"
    r"|\b\d[\d\s.,]*\s*(?:ht|ttc|hors\s+taxes?|toutes\s+taxes|/\s*mois)\b",
    re.IGNORECASE,
)


def contains_price(*parts):
    # URLs carry arbitrary tokens; do not interpret them as prose or currencies.
    text = "\n".join(str(part or "") for part in parts)
    text = re.sub(r"https?://\S+", "", text)
    return bool(PRICE.search(unicodedata.normalize("NFKC", text)))


def validate(*parts):
    if contains_price(*parts):
        raise ValueError(REASON)
