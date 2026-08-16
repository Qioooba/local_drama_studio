"""Bounded HTTP transport for local adapters.

Local adapters must never inherit a process-wide proxy or follow a redirect
outside the operator-owned loopback service.  Keeping this policy in one
opener prevents ComfyUI and Local LLM clients from drifting apart.
"""

from __future__ import annotations

from email.message import Message
from typing import IO
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, OpenerDirector, ProxyHandler, Request, build_opener
from urllib.response import addinfourl


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: IO[bytes] | None, code: int, msg: str, headers: Message, newurl: str) -> Request:
        raise HTTPError(req.full_url, code, "loopback redirect blocked", headers, fp)


# An explicit empty ProxyHandler ignores HTTP(S)_PROXY/ALL_PROXY environment
# variables.  The redirect handler fails before a Location target is opened.
LOCAL_HTTP_OPENER: OpenerDirector = build_opener(ProxyHandler({}), _RejectRedirectHandler())


def open_local(request: Request, *, timeout: float) -> addinfourl:
    """Open one already-validated local request without proxy/redirect hops."""

    return LOCAL_HTTP_OPENER.open(request, timeout=timeout)
