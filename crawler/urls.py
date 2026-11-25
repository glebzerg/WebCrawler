from urllib.parse import urljoin, urlsplit, urlunsplit


def resolve_http_url(base_url: str, reference: str) -> str | None:
    reference = reference.strip()
    if not reference:
        return None

    try:
        source = urlsplit(reference)
        if (source.scheme or reference.startswith("//")) and not source.hostname:
            return None

        url = urljoin(base_url, reference)
        address = urlsplit(url)
        host = address.hostname
        if address.scheme not in {"http", "https"} or not host:
            return None
        if any(character.isspace() for character in host):
            return None

        # Accessing port validates its format and range.
        _ = address.port
    except ValueError:
        return None

    return url


def url_key(url: str) -> str:
    """Build a comparison key by normalizing scheme and host and removing the fragment."""
    parts = urlsplit(url)
    user_info, at_sign, authority = parts.netloc.rpartition("@")
    host = parts.hostname or ""

    if authority.startswith("["):
        _, _, port_suffix = authority.partition("]")
        authority = f"[{host}]{port_suffix}"
    else:
        _, colon, port = authority.partition(":")
        authority = f"{host}{colon}{port}"

    key = urlunsplit(
        (parts.scheme, f"{user_info}{at_sign}{authority}", parts.path, parts.query, "")
    )
    if not parts.query and url.partition("#")[0].endswith("?"):
        return f"{key}?"
    return key
