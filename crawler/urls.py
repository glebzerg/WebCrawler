from urllib.parse import urlsplit, urlunsplit


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
