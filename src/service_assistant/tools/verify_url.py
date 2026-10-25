import ipaddress
import re
from urllib.parse import urlsplit

HOST_PATTERN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
PATH_PATTERN = re.compile(r"/[A-Za-z0-9/_~.\-]*$")


def public_hostname(host: str) -> bool:
    if not HOST_PATTERN.fullmatch(host) or host.endswith(
        (".localhost", ".local", ".internal", ".test", ".invalid", ".example")
    ):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return not any(label.startswith("xn--") for label in host.split("."))


def safe_path(path: str) -> bool:
    return (
        bool(PATH_PATTERN.fullmatch(path))
        and not any(part in (".", "..") for part in path.split("/"))
        and "//" not in path
    )


def verify_url(url: str, hostname: str, allowed_paths: tuple[str, ...]) -> str | None:
    """Exact offline authority/path verification. Does not resolve DNS or make HTTP requests.

    Only a small canonical HTTPS URL subset is accepted. Queries, fragments,
    encoded paths, userinfo, ports, Unicode hosts and subdomain wildcards are excluded.
    """
    try:
        parsed = urlsplit(url)
        if (
            not url.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or parsed.scheme != "https"
            or not public_hostname(hostname)
            or parsed.netloc != hostname
            or parsed.query
            or parsed.fragment
            or "?" in url
            or "#" in url
            or not safe_path(parsed.path)
            or parsed.path not in allowed_paths
        ):
            return "unsafe_or_unapproved_url"
    except (ValueError, UnicodeError):
        return "unsafe_or_unapproved_url"
    return None
