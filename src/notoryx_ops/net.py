"""Outbound HTTPS with a certificate bundle that works on every install.

Python from python.org on macOS ships without trusted root certificates, so plain
urllib fails with CERTIFICATE_VERIFY_FAILED until someone runs "Install Certificates".
The certifi bundle avoids that; the system store is the fallback.
"""

from __future__ import annotations

import ssl
import urllib.request
from functools import lru_cache


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover
        return ssl.create_default_context()


def urlopen(req: urllib.request.Request | str, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout, context=tls_context())
