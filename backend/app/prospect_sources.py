"""Small, bounded reads of company websites and the public French business registry."""

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.links = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())[:40000]


def public_url(url, domain):
    u = urlsplit(url)
    if u.scheme not in {"https", "http"} or u.username or u.password or u.port not in {None, 80, 443}:
        raise ValueError("Adresse web non autorisée")
    if (u.hostname or "").removeprefix("www.") != domain.removeprefix("www."):
        raise ValueError("Lien hors du site de l’entreprise")
    addresses = socket.getaddrinfo(
        u.hostname, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM
    )
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Adresse non publique")
    return url


def read_page(url, domain):
    with httpx.Client(
        timeout=httpx.Timeout(8, connect=4),
        follow_redirects=False,
        headers={"User-Agent": "LumeliaOutreach/1.0"},
    ) as client:
        for _ in range(4):
            public_url(url, domain)
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", "").lower():
                    raise ValueError("Page non HTML")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 500000:
                        break
                raw = bytes(body[:500000])
                encoding = (
                    "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else response.encoding or "utf-8"
                )
                decoded = raw.decode(encoding, errors="replace")
                if "\x00" in decoded or decoded.count("\ufffd") > max(5, len(decoded) // 50):
                    raise ValueError("Page illisible : encodage ou contenu binaire")
                page = Page()
                page.feed(decoded)
                return {"url": url, "text": page.text, "links": page.links}
    raise ValueError("Trop de redirections")


def valid_siren(value):
    if not re.fullmatch(r"\d{9}", value):
        return False
    digits = [int(x) for x in value]
    return sum((v * 2 // 10 + v * 2 % 10) if i % 2 else v for i, v in enumerate(digits)) % 10 == 0


def collect_site(domain):
    pages = []
    errors = []
    try:
        first = read_page("https://" + domain, domain)
        pages.append(first)
        links = []
        for href in first["links"]:
            if re.search(r"mention|legal|contact|equipe|about|qui-sommes", href, re.I):
                url = urljoin(first["url"], href)
                if urlsplit(url).hostname in {domain, "www." + domain} and url not in links:
                    links.append(url)
        for url in sorted(links, key=lambda x: 0 if re.search("mention|legal", x, re.I) else 1)[:2]:
            try:
                pages.append(read_page(url, domain))
            except (httpx.HTTPError, ValueError, OSError):
                errors.append("Page secondaire indisponible")
    except (httpx.HTTPError, ValueError, OSError):
        errors.append("Site indisponible pour la lecture automatique")
    sirens = {}
    emails = {}
    for p in pages:
        for value in re.findall(
            r"(?:SIREN|SIRET|RCS)\s*(?:n[°o]?|num[ée]ro)?\s*[:.\-]?\s*([\d\s.]{9,22})", p["text"], re.I
        ):
            digits = re.sub(r"\D", "", value)
            if len(digits) in {9, 14} and valid_siren(digits[:9]):
                sirens[digits[:9]] = p["url"]
        for value in re.findall(
            r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", p["text"] + " " + " ".join(p["links"])
        ):
            value = value.lower().rstrip(".")
            if value.split("@")[-1].removeprefix("www.") == domain.removeprefix("www."):
                emails[value] = p["url"]
    return {"pages": pages, "sirens": sirens, "emails": emails, "errors": errors}


def registry(siren):
    response = httpx.get(
        "https://recherche-entreprises.api.gouv.fr/search", params={"q": siren, "per_page": 1}, timeout=12
    )
    response.raise_for_status()
    rows = [
        r
        for r in response.json().get("results", [])
        if r.get("siren") == siren and r.get("statut_diffusion") == "O"
    ]
    if len(rows) != 1:
        return None
    r = rows[0]
    # Keep professional identity only; never retain birth dates, nationality or home addresses.
    return {
        "siren": siren,
        "name": r.get("nom_complet"),
        "active": r.get("etat_administratif") == "A",
        "naf": r.get("activite_principale"),
        "size_code": r.get("tranche_effectif_salarie"),
        "size_year": r.get("annee_tranche_effectif_salarie"),
        "source_url": "https://annuaire-entreprises.data.gouv.fr/entreprise/" + siren,
        "directors": [
            {
                "first_name": d.get("prenoms") or "",
                "last_name": d.get("nom") or "",
                "role": d.get("qualite") or "",
            }
            for d in r.get("dirigeants", [])
            if d.get("prenoms") and d.get("nom")
        ][:5],
    }


SIZE_RANGES = {
    "00": (0, 0),
    "01": (1, 2),
    "02": (3, 5),
    "03": (6, 9),
    "11": (10, 19),
    "12": (20, 49),
    "21": (50, 99),
    "22": (100, 199),
    "31": (200, 249),
    "32": (250, 499),
    "41": (500, 999),
    "42": (1000, 1999),
    "51": (2000, 4999),
    "52": (5000, 9999),
    "53": (10000, 10000000),
}
