from urllib.parse import urlparse


_SECOND_LEVEL = {"co.uk", "org.uk", "com.au", "com.br", "co.jp", "co.nz", "com.mx",
                 "gouv.fr", "asso.fr", "com.fr"}


def registrable_domain(host: str | None) -> str | None:
    """Domaine « de base » d'un hôte (heuristique : ex. blog.exemple.com -> exemple.com)."""
    if not host:
        return None
    labels = host.lower().rstrip(".").removeprefix("www.").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    keep = 3 if ".".join(labels[-2:]) in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def normalize_domain(value: str) -> str:
    """Normalise un domaine ou une URL (minuscules, sans schéma, chemin ni « www. »).

    Lève ValueError si la valeur ne ressemble pas à un nom de domaine.
    """
    raw = (value or "").strip().lower()
    if not raw:
        raise ValueError("Le domaine est obligatoire.")
    host = urlparse(raw if "://" in raw else f"//{raw}").hostname or ""
    host = host.removeprefix("www.").rstrip(".")
    labels = host.split(".")
    if len(labels) < 2 or any(not label for label in labels) or " " in host:
        raise ValueError(f"Domaine invalide : {value!r}")
    return host
