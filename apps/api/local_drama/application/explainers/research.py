"""Research, source and fact-extraction services for the explainer factory (W02).

Three boundaries are implemented here and must not be softened elsewhere:

**1. Untrusted text is data, never instruction.**
Source pages and imported documents are attacker-influenced input.  They are
always passed as a *separate* parameter from the trusted system instruction
(:func:`compose_extraction_request`), the model response is accepted only through
:data:`FACT_EXTRACTION_SCHEMA` (every object node declares
``additionalProperties: false``, and :func:`validate_model_payload` rejects a
schema without that declaration as well as any undeclared field), HTML is reduced
to text by :func:`html_to_text` where ``<script>``/``<style>`` bodies are dropped
before anything reads them, and :func:`looks_like_injection` exists purely to
*report* suspicious markers.  Nothing in this module branches on those markers.

**2. Public fetching cannot reach the local network.**
:class:`ControlledFetcher` allows only ``http``/``https``, re-validates every
redirect hop, resolves each hostname and denies the fetch when *any* resolved
address is non-public, and additionally checks the peer address of the socket
that was actually connected (``_GuardedHTTPConnection``) so a DNS-rebinding
answer cannot slip through the window between resolution and connect.  Loopback,
private, link-local, unspecified, reserved, multicast, ``169.254.169.254`` /
``metadata.google.internal`` / ``.internal`` / ``.local`` / ``.onion`` and
non-HTTP schemes are refused with ``status="DENIED"``.  Environment proxies are
disabled (``ProxyHandler({})``) so the peer check always inspects the real
target rather than a proxy.  Known residual limit, stated rather than hidden: a
public IP that the *peer itself* forwards to a private service cannot be seen
from here.

**3. Offline import performs zero network calls.**
``ResearchMode.OFFLINE_IMPORT`` has no code path that opens a socket: the fetcher
is only consulted from :meth:`ExplainerResearchService.register_url_source`, and
that method refuses offline packets before anything else happens.  A packet that
wants network research must be created as ``WEB_RESEARCH`` with a positive
request budget, and every request is counted by
:meth:`ExplainerResearchService.record_external_request`.

All SQL goes through :class:`ExplainerRepository`; this module only reads through
``find``/``list_where``/``get`` and writes through ``insert``/``update``/``bump``
/``mark_dependents_stale``.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import urllib.error
import urllib.request
import zlib
from collections.abc import Mapping, Sequence
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any, NoReturn
from urllib.parse import unquote, urljoin, urlsplit

from local_drama.application.explainers.sources import (
    classify_source_credibility,
    normalise_document_text,
    normalise_event_date,
    split_paragraph_spans,
)
from local_drama.domain.explainers.contracts import (
    ERROR_NEXT_STEP,
    PREFLIGHT_CATEGORIES,
    SCHEMA_VERSION,
    ClaimStatus,
    ContentKind,
    CredibilityKind,
    DatePrecision,
    EntityType,
    EvidenceStance,
    ExplainerContractError,
    ExplainerErrorCode,
    ResearchMode,
    RetrievalVia,
    StatementType,
    classify_blocker,
    content_hash,
    normalize_locale,
    text_hash,
    utc_now_iso,
)
from local_drama.domain.explainers.policies import AssetLicense, evaluate_license_scope
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
HTTP_SCHEMES: frozenset[str] = frozenset({"http", "https"})

#: Statuses urllib would normally follow by itself; the fetch loop follows them
#: manually so every hop passes :meth:`ControlledFetcher._validate_target`.
REDIRECT_STATUS_CODES: frozenset[int] = frozenset({301, 302, 303, 307, 308})

#: Streaming read size.  Kept small so a hostile server cannot make the process
#: allocate the whole body before the cap is checked.
READ_CHUNK_BYTES = 65_536

#: Hosts/addresses that answer with cloud instance credentials.
METADATA_HOSTS: frozenset[str] = frozenset(
    {"metadata.google.internal", "metadata.goog", "169.254.169.254", "100.100.100.200", "fd00:ec2::254"}
)
METADATA_ADDRESSES: frozenset[str] = frozenset({"169.254.169.254", "100.100.100.200", "fd00:ec2::254"})

#: Classification severity order used when several resolved addresses disagree.
CLASSIFICATION_SEVERITY: tuple[str, ...] = ("METADATA", "LOOPBACK", "LINK_LOCAL", "PRIVATE", "UNKNOWN")

#: Dependency-graph vocabulary for the research stage.  ``upstream_kind`` is
#: free-form text, but ``downstream_kind`` is constrained by
#: ``ck_artifact_dependencies_downstream`` in migration 0102, so only artifacts
#: from that list may be named here.  No edge is *written* at research time:
#: there is no downstream artifact yet, and inventing a downstream id would
#: corrupt the staleness graph.  Later stages register the real edges; this
#: module only propagates staleness to them when a packet is superseded.
UPSTREAM_KIND_RESEARCH_PACKET = "RESEARCH_PACKET"
DEPENDENT_KINDS_OF_RESEARCH_PACKET: tuple[str, ...] = (
    "SCRIPT_SEGMENT",
    "VISUAL_BEAT",
    "BEAT_SELECTION",
    "COMPOSITION_REVISION",
)

#: Hard cap on any array accepted from a model response (bounded work, bounded
#: prompt-injection payload surface).
MAX_MODEL_ARRAY_ITEMS = 2_000

#: Quality-gate rule version, recorded with every gate result.
CONTENT_GATE_RULE_VERSION = "explainer_content_gate_v1"

CLAIM_IMPORTANCE_BLOCKING: frozenset[str] = frozenset({"CORE", "KEY"})

_DOMAIN_CHARS_RE = re.compile(r"[^a-z0-9.\-]")
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)")


# --------------------------------------------------------------------------- #
# address classification (SSRF boundary)
# --------------------------------------------------------------------------- #
def classify_ip_literal(value: str) -> str | None:
    """Classify a literal IP address, or return ``None`` when it is not one.

    ``METADATA`` is tested before link-local/private because
    ``169.254.169.254`` is simultaneously link-local and private, and reporting
    it as "just private" would hide the credential-service case.
    """

    candidate = (value or "").strip().strip("[]")
    if "%" in candidate:  # IPv6 zone id, e.g. fe80::1%eth0
        candidate = candidate.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if address.is_loopback:
        return "LOOPBACK"
    if str(address) in METADATA_ADDRESSES:
        return "METADATA"
    if address.is_link_local:
        return "LINK_LOCAL"
    if address.is_private:
        return "PRIVATE"
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        return "UNKNOWN"
    if not address.is_global:
        return "UNKNOWN"
    return "PUBLIC"


def classify_host(host: str) -> str:
    """Classify a host as ``PUBLIC`` or as a reason to deny the fetch.

    A name that is not an IP literal is resolved with ``socket.getaddrinfo`` and
    **every** answer is classified: one private answer among public ones is
    enough to deny, because the connection could be made to either.  Denied
    suffixes are checked before resolution so ``.internal``/``.local``/``.onion``
    cannot be rescued by a public DNS answer.
    """

    folded = (host or "").strip().strip("[]").casefold().rstrip(".")
    if not folded:
        return "UNKNOWN"
    if folded in METADATA_HOSTS:
        return "METADATA"
    if folded.endswith(".internal"):
        return "METADATA"
    if folded.endswith(".local"):
        return "LINK_LOCAL"
    if folded.endswith(".onion"):
        return "UNKNOWN"
    if folded == "localhost" or folded.endswith(".localhost"):
        return "LOOPBACK"

    literal = classify_ip_literal(folded)
    if literal is not None:
        return literal

    try:
        infos = socket.getaddrinfo(folded, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError):
        return "UNKNOWN"
    addresses = {str(info[4][0]) for info in infos if len(info) > 4 and info[4]}
    if not addresses:
        return "UNKNOWN"
    verdicts = {classify_ip_literal(address) or "UNKNOWN" for address in addresses}
    if verdicts == {"PUBLIC"}:
        return "PUBLIC"
    for label in CLASSIFICATION_SEVERITY:
        if label in verdicts:
            return label
    return "UNKNOWN"


def _normalise_domain_rule(value: str) -> str:
    return _DOMAIN_CHARS_RE.sub("", (value or "").strip().casefold().rstrip("."))


def domain_is_allowed(host: str, allowed_domains: Sequence[str]) -> bool:
    """Whether *host* matches the allow-list (exact, or a subdomain of an entry).

    An empty allow-list means "no domain restriction", never "allow everything
    including the local network" — the SSRF check runs independently.
    """

    rules = [
        _normalise_domain_rule(rule[2:] if rule.strip().startswith("*.") else rule)
        for rule in allowed_domains
        if _normalise_domain_rule(rule)
    ]
    if not rules:
        return True
    folded = (host or "").strip().casefold().rstrip(".")
    return any(folded == rule or folded.endswith(f".{rule}") for rule in rules)


def normalise_domain_list(allowed_domains: Sequence[str]) -> list[str]:
    """Deterministic, de-duplicated allow-list used in packet hashes."""

    seen: list[str] = []
    for rule in allowed_domains or ():
        cleaned = _normalise_domain_rule(str(rule))
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


# --------------------------------------------------------------------------- #
# prompt-injection reporting (reporting only - never a control flow input)
# --------------------------------------------------------------------------- #
def looks_like_injection(text: str) -> list[str]:
    """Return matched suspicious markers found in *text*.

    The result is *reporting only*.  Callers may show it to a human, and must not
    use it to decide whether content is trusted or how content is interpreted:
    an article about prompt injection legitimately contains the phrase "ignore
    previous instructions", and treating that as an instruction (or as guilt)
    would be the same category error as executing it.
    """

    haystack = (text or "").casefold()
    if not haystack:
        return []
    matched: list[str] = []
    for group in _INJECTION_MARKER_GROUPS:
        for marker in group:
            if marker in haystack and marker not in matched:
                matched.append(marker)
    return matched


_INJECTION_MARKER_GROUPS: tuple[tuple[str, ...], ...] = (
    (
        "忽略系统",
        "忽略以上",
        "忽略上述",
        "忽略之前",
        "忽略先前",
        "忽略安全",
        "ignore previous",
        "ignore all previous",
        "ignore the above",
        "disregard previous",
        "forget previous",
    ),
    (
        "system prompt",
        "系统提示",
        "系统提示词",
        "系统指令",
        "developer message",
        "hidden instruction",
        "隐藏指令",
    ),
    (
        "upload local file",
        "上传本地文件",
        "读取本地文件",
        "打开本地文件",
        "id_rsa",
        ".env",
        "ssh key",
        "api key",
        "api_key",
        "access token",
    ),
    (
        "curl ",
        "wget ",
        "powershell",
        "cmd.exe",
        "/bin/sh",
        "rm -rf",
        "base64 -d",
        "执行命令",
        "运行命令",
    ),
    (
        "<script",
        "javascript:",
        "onerror=",
        "onload=",
        "data:text/html",
        "<iframe",
        "<?php",
    ),
    (
        "you are now",
        "from now on you",
        "从现在开始你",
        "jailbreak",
        "dan mode",
        "disable safety",
        "忽略内容政策",
    ),
)


# --------------------------------------------------------------------------- #
# HTML -> text
# --------------------------------------------------------------------------- #
#: Tags whose content is code or styling, not prose.  ``head`` is skipped whole
#: so ``<title>``/``<meta>`` text never becomes citable "evidence".
_MARKUP_SKIP_TAGS: frozenset[str] = frozenset(
    {"script", "style", "noscript", "template", "head", "svg", "canvas", "iframe", "object", "embed"}
)
#: Tags that start a new block: they become paragraph breaks.
_MARKUP_BLOCK_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "nav",
        "aside",
        "form",
        "address",
        "blockquote",
        "pre",
        "figure",
        "figcaption",
        "ul",
        "ol",
        "li",
        "dd",
        "dt",
        "table",
        "thead",
        "tbody",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)
_MARKUP_LINE_TAGS: frozenset[str] = frozenset({"br", "hr"})


class _MarkupTextExtractor(HTMLParser):
    """Collect visible prose from HTML with paragraph breaks preserved."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        if name in _MARKUP_SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if name in _MARKUP_BLOCK_TAGS:
            self._chunks.append("\n\n")
        elif name in _MARKUP_LINE_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in _MARKUP_SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if name in _MARKUP_BLOCK_TAGS:
            self._chunks.append("\n\n")
        elif name in _MARKUP_LINE_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._chunks.append(data)

    def extracted_text(self) -> str:
        return "".join(self._chunks)


def _charset_of(content_type: str) -> str:
    matched = _CHARSET_RE.search(content_type or "")
    return matched.group(1) if matched else ""


def _decode_payload(raw: bytes, content_type: str) -> str:
    """Decode bytes using the declared charset, then UTF-8, then GB18030.

    Unlike :func:`local_drama.application.explainers.sources.decode_document_bytes`
    this never raises: a fetched page that decodes badly must still become a
    reportable source (with its raw hash) instead of aborting the fetch.
    """

    candidates: list[str] = []
    declared = _charset_of(content_type)
    if declared:
        candidates.append(declared)
    candidates.extend(["utf-8", "gb18030"])
    for candidate in candidates:
        try:
            return raw.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _tidy_text(value: str) -> str:
    text = value.replace("\xa0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_markup(content_type: str, payload: bytes) -> bool:
    folded = (content_type or "").casefold()
    if any(token in folded for token in ("html", "xhtml", "xml", "rss", "atom")):
        return True
    head = payload[:2048].lstrip().casefold()
    return any(
        head.startswith(token)
        for token in (b"<!doctype html", b"<html", b"<body", b"<div", b"<?xml", b"<rss", b"<feed")
    ) or b"<body" in head


def html_to_text(raw: bytes, content_type: str) -> str:
    """Reduce ``raw`` to plain text: no scripts, no styles, no tags.

    The function is deliberately unconditional — it runs the stdlib
    ``html.parser`` extractor even for ``text/plain`` input.  ``<script>`` and
    ``<style>`` bodies are dropped before their content reaches any caller, so a
    page cannot smuggle "instructions" into the extracted source text through a
    script element.  Nothing here interprets the text: it only normalises it.
    """

    if not raw:
        return ""
    decoded = _decode_payload(raw, content_type)
    parser = _MarkupTextExtractor()
    try:
        parser.feed(decoded)
        parser.close()
    except Exception:  # noqa: BLE001 - a malformed page must not fail the fetch
        return _tidy_text(decoded)
    return _tidy_text(parser.extracted_text())


# --------------------------------------------------------------------------- #
# controlled fetching
# --------------------------------------------------------------------------- #
class _NonPublicPeerError(OSError):
    """Raised when the socket's real peer address is not a public address.

    Subclasses ``OSError`` on purpose: ``urllib`` wraps ``OSError`` from
    ``connect`` into ``URLError``, which the fetch loop unwraps to report a
    DENIED result instead of a generic network error.
    """

    def __init__(self, classification: str, peer: str) -> None:
        super().__init__(f"NON_PUBLIC_PEER:{classification}:{peer}")
        self.classification = classification
        self.peer = peer


def _check_peer(sock: socket.socket | None) -> None:
    if sock is None:
        raise _NonPublicPeerError("UNKNOWN", "NO_SOCKET")
    try:
        peer = str(sock.getpeername()[0])
    except OSError as error:  # pragma: no cover - defensive
        raise _NonPublicPeerError("UNKNOWN", f"PEER_UNREADABLE:{type(error).__name__}") from error
    classification = classify_ip_literal(peer) or classify_host(peer)
    if classification != "PUBLIC":
        sock.close()
        raise _NonPublicPeerError(classification, peer)


class _GuardedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that re-checks the address actually connected to."""

    def connect(self) -> None:
        super().connect()
        _check_peer(self.sock)


class _GuardedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection with the same post-connect peer check."""

    def connect(self) -> None:
        super().connect()
        _check_peer(self.sock)


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(_GuardedHTTPConnection, req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(
            _GuardedHTTPSConnection,
            req,
            context=getattr(self, "_context", None),
            check_hostname=getattr(self, "_check_hostname", None),
        )


class _RefuseRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect inside urllib; the fetch loop re-validates it."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def build_guarded_opener() -> urllib.request.OpenerDirector:
    """Opener with proxies disabled and every connection peer-checked.

    ``ProxyHandler({})`` is not cosmetic: if an environment proxy were used, the
    peer address check would inspect the proxy instead of the target and the
    whole SSRF guarantee would silently become meaningless.
    """

    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RefuseRedirectHandler(),
        _GuardedHTTPHandler(),
        _GuardedHTTPSHandler(),
    )


class ControlledFetcher:
    """Bounded, deny-by-default public fetch client.

    Every limit is enforced while streaming (never by trusting
    ``Content-Length`` alone) and every refusal is returned as a structured dict
    — :meth:`fetch` never raises, because a research step must always be able to
    record *why* a source could not be acquired.
    """

    def __init__(
        self,
        *,
        allowed_domains: Sequence[str] = (),
        timeout_seconds: float = 10.0,
        max_redirects: int = 5,
        max_response_bytes: int = 4_000_000,
        max_total_bytes: int = 16_000_000,
        user_agent: str = "LocalDramaStudio-ExplainerResearch/1.0",
        request_log: list | None = None,
    ) -> None:
        self.allowed_domains = tuple(normalise_domain_list(allowed_domains))
        self.timeout_seconds = float(timeout_seconds)
        self.max_redirects = int(max_redirects)
        self.max_response_bytes = int(max_response_bytes)
        self.max_total_bytes = int(max_total_bytes)
        self.user_agent = user_agent
        self.request_log: list[Any] = request_log if request_log is not None else []
        self._opener = build_guarded_opener()

    # ------------------------------------------------------------------ public
    def fetch(self, url: str) -> dict[str, Any]:
        """Fetch *url* under the SSRF/byte/redirect policy.

        Returned keys are always present.  ``body_text`` is the HTML-reduced
        text, while ``body_sha256`` pins the *received* bytes (after
        decompression, before tag stripping) so two fetches can be compared even
        if the text extractor changes later.  ``redirect_chain`` lists every URL
        this fetcher actually contacted, in order.
        """

        result: dict[str, Any] = {
            "status": "ERROR",
            "final_url": str(url or ""),
            "status_code": None,
            "body_text": "",
            "body_bytes": 0,
            "body_sha256": None,
            "content_type": "",
            "redirect_chain": [],
            "denied_reason": None,
            "classification": "UNKNOWN",
        }
        try:
            self._fetch(str(url or "").strip(), result)
        except Exception as error:  # noqa: BLE001 - the fetcher contract is "never raise"
            result["status"] = "ERROR"
            result["denied_reason"] = f"UNEXPECTED_{type(error).__name__}"
            self._log(result)
        return result

    # ----------------------------------------------------------------- internal
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.1",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "close",
        }

    def _validate_target(self, url: str) -> tuple[str, str] | None:
        parsed = urlsplit(url or "")
        scheme = (parsed.scheme or "").casefold()
        if scheme not in HTTP_SCHEMES:
            return "UNKNOWN", "SCHEME_NOT_ALLOWED"
        if not parsed.hostname:
            return "UNKNOWN", "HOST_MISSING"
        if parsed.username or parsed.password:
            return "UNKNOWN", "URL_CREDENTIALS_NOT_ALLOWED"
        host = parsed.hostname
        classification = classify_host(host)
        if classification != "PUBLIC":
            return classification, f"NON_PUBLIC_ADDRESS:{classification}"
        if not domain_is_allowed(host, self.allowed_domains):
            return "PUBLIC", "DOMAIN_NOT_ALLOWED"
        return None

    def _denied(self, result: dict[str, Any], url: str, chain: list[str], classification: str, reason: str) -> None:
        result.update(
            status="DENIED",
            final_url=url,
            redirect_chain=list(chain),
            denied_reason=reason,
            classification=classification,
        )
        self._log(result)

    def _error(self, result: dict[str, Any], url: str, chain: list[str], reason: str, status_code: int | None = None) -> None:
        result.update(
            status="ERROR",
            final_url=url,
            status_code=status_code,
            redirect_chain=list(chain),
            denied_reason=reason,
        )
        self._log(result)

    def _fetch(self, url: str, result: dict[str, Any]) -> None:
        current = url
        chain: list[str] = []
        total_bytes = 0

        while True:
            guard = self._validate_target(current)
            if guard is not None:
                self._denied(result, current, chain, guard[0], guard[1])
                return
            if len(chain) > self.max_redirects:
                self._denied(result, current, chain, "UNKNOWN", "TOO_MANY_REDIRECTS")
                return
            chain.append(current)

            request = urllib.request.Request(current, headers=self._headers(), method="GET")
            try:
                response = self._opener.open(request, timeout=self.timeout_seconds)
            except urllib.error.HTTPError as error:
                code = int(error.code or 0)
                headers = error.headers
                location = headers.get("Location") if headers is not None else None
                error.close()
                if code in REDIRECT_STATUS_CODES:
                    if not location:
                        self._denied(result, current, chain, "UNKNOWN", "REDIRECT_WITHOUT_LOCATION")
                        return
                    current = urljoin(current, location)
                    continue
                self._error(result, current, chain, f"HTTP_STATUS_{code}", status_code=code)
                return
            except urllib.error.URLError as error:
                reason = error.reason
                if isinstance(reason, _NonPublicPeerError):
                    self._denied(
                        result, current, chain, reason.classification, f"NON_PUBLIC_PEER:{reason.peer}"
                    )
                    return
                if isinstance(reason, TimeoutError):
                    self._error(result, current, chain, "TIMEOUT")
                    return
                self._error(result, current, chain, f"NETWORK_ERROR:{type(reason).__name__}")
                return
            except TimeoutError:
                self._error(result, current, chain, "TIMEOUT")
                return
            except (OSError, http.client.HTTPException) as error:
                self._error(result, current, chain, f"NETWORK_ERROR:{type(error).__name__}")
                return

            with response:
                content_type = response.headers.get("Content-Type") or ""
                declared_length = response.headers.get("Content-Length")
                if declared_length and declared_length.strip().isdigit():
                    if int(declared_length) > self.max_response_bytes:
                        self._denied(result, current, chain, "PUBLIC", "RESPONSE_TOO_LARGE")
                        return
                content_encoding = (response.headers.get("Content-Encoding") or "").strip().casefold()
                payload, denial = self._read_payload(response, content_encoding, total_bytes)
                if denial is not None:
                    self._denied(result, current, chain, "PUBLIC", denial)
                    return
                total_bytes += len(payload)
                final_url = str(response.geturl() or current)
                if final_url != current:
                    # Defensive: the redirect handler should already have made
                    # this impossible, but an unvalidated hop must never be
                    # reported as a successful public fetch.
                    late_guard = self._validate_target(final_url)
                    if late_guard is not None:
                        self._denied(result, final_url, chain, late_guard[0], "UNVALIDATED_REDIRECT")
                        return
                    chain.append(final_url)
                body_text = html_to_text(payload, content_type) if _looks_like_markup(content_type, payload) else _tidy_text(_decode_payload(payload, content_type))
                result.update(
                    status="OK",
                    status_code=int(response.status),
                    final_url=final_url,
                    content_type=content_type,
                    body_text=body_text,
                    body_bytes=len(payload),
                    body_sha256=hashlib.sha256(payload).hexdigest(),
                    redirect_chain=list(chain),
                    denied_reason=None,
                    classification="PUBLIC",
                )
                self._log(result)
                return

    def _read_payload(
        self, response: Any, content_encoding: str, already_read: int
    ) -> tuple[bytes, str | None]:
        """Stream the body under the per-response and total caps."""

        chunks: list[bytes] = []
        read = 0
        while True:
            chunk = response.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            read += len(chunk)
            if read > self.max_response_bytes:
                return b"", "RESPONSE_TOO_LARGE"
            if already_read + read > self.max_total_bytes:
                return b"", "TOTAL_BYTES_EXCEEDED"
            chunks.append(chunk)
        return self._decompress(b"".join(chunks), content_encoding, already_read)

    def _decompress(self, payload: bytes, content_encoding: str, already_read: int) -> tuple[bytes, str | None]:
        """Bounded decompression: a zip-bomb body is refused, not expanded."""

        encoding = (content_encoding or "").strip().casefold()
        if not encoding or encoding == "identity":
            return payload, None
        if encoding in {"gzip", "x-gzip"}:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            decompressor = zlib.decompressobj()
        else:
            return b"", f"UNSUPPORTED_CONTENT_ENCODING:{encoding}"

        limit = self.max_response_bytes
        out = bytearray()
        try:
            for index in range(0, len(payload), READ_CHUNK_BYTES):
                chunk = payload[index : index + READ_CHUNK_BYTES]
                out.extend(decompressor.decompress(chunk, limit + 1 - len(out)))
                if len(out) > limit:
                    return b"", "DECOMPRESSED_TOO_LARGE"
                if already_read + len(out) > self.max_total_bytes:
                    return b"", "TOTAL_BYTES_EXCEEDED"
            out.extend(decompressor.flush())
        except zlib.error:
            return b"", "DECOMPRESSION_FAILED"
        if len(out) > limit:
            return b"", "DECOMPRESSED_TOO_LARGE"
        return bytes(out), None

    def _log(self, result: Mapping[str, Any]) -> None:
        chain = result.get("redirect_chain") or []
        self.request_log.append(
            {
                "url": str(result.get("final_url") or ""),
                "status": str(result.get("status")),
                "status_code": result.get("status_code"),
                "classification": str(result.get("classification")),
                "denied_reason": result.get("denied_reason"),
                "body_bytes": int(result.get("body_bytes") or 0),
                "redirect_hops": len(chain),
                "at": utc_now_iso(),
            }
        )


# --------------------------------------------------------------------------- #
# model response schema + validation
# --------------------------------------------------------------------------- #
_STRING: dict[str, Any] = {"type": "string"}
_BOOLEAN: dict[str, Any] = {"type": "boolean"}
_INTEGER: dict[str, Any] = {"type": "integer"}

#: The only shape :meth:`ExplainerResearchService.apply_fact_extraction` accepts.
#: Independence, status and confidence are deliberately *not* requested from the
#: model: they are derived from recorded provenance so a persuasive response
#: cannot promote its own claims.  No field here is a free-form object, because
#: ``additionalProperties: false`` would make such a field carry nothing.
FACT_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "statement"],
                "properties": {
                    "code": _STRING,
                    "statement": _STRING,
                    "statement_kind": {"type": "string", "enum": [item.value for item in StatementType]},
                    "importance": {"type": "string", "enum": ["CORE", "KEY", "SUPPORTING"]},
                    "confidence_reason": _STRING,
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["source_span_id", "stance"],
                            "properties": {
                                "source_span_id": _STRING,
                                "source_id": _STRING,
                                "stance": {"type": "string", "enum": [item.value for item in EvidenceStance]},
                                "note": _STRING,
                            },
                        },
                    },
                },
            },
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "title"],
                "properties": {
                    "code": _STRING,
                    "title": _STRING,
                    "story_time_start": _STRING,
                    "story_time_end": _STRING,
                    "calendar_system": _STRING,
                    "place_label": _STRING,
                    "participant_entity_codes": {"type": "array", "items": _STRING},
                    "claim_codes": {"type": "array", "items": _STRING},
                    "causal_note": _STRING,
                    "sequence_no": _INTEGER,
                },
            },
        },
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "name", "entity_type"],
                "properties": {
                    "code": _STRING,
                    "name": _STRING,
                    "entity_type": {"type": "string", "enum": [item.value for item in EntityType]},
                    "latin_name": _STRING,
                    "aliases": {"type": "array", "items": _STRING},
                    "descriptive_only": _BOOLEAN,
                    "state": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [],
                        "properties": {
                            "label": _STRING,
                            "age": _INTEGER,
                            "wardrobe": _STRING,
                            "condition": _STRING,
                            "valid_from_story_time": _STRING,
                            "valid_to_story_time": _STRING,
                            "carried_prop_entity_codes": {"type": "array", "items": _STRING},
                        },
                    },
                },
            },
        },
    },
}


def _schema_fail(path: str, message: str, details: Mapping[str, Any] | None = None) -> NoReturn:
    payload = {"path": path, **dict(details or {})}
    raise ExplainerContractError("SCHEMA_INVALID", message, payload)


def _validate_node(value: Any, schema: Mapping[str, Any], *, path: str) -> Any:
    if not isinstance(schema, Mapping):
        _schema_fail(path, f"{path}：schema 节点无效")
    expected = schema.get("type")
    if not isinstance(expected, str):
        _schema_fail(path, f"{path}：schema 未声明 type")

    if expected == "object":
        if schema.get("additionalProperties") is not False:
            # A permissive schema would let a model inject fields the service
            # later persists by name; refusing here keeps the boundary explicit.
            _schema_fail(path, f"{path}：模型响应 schema 必须声明 additionalProperties: false")
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            _schema_fail(path, f"{path}：应为对象", {"received_type": type(value).__name__})
        properties = schema.get("properties") or {}
        if not isinstance(properties, Mapping):
            _schema_fail(path, f"{path}：schema properties 无效")
        required = [str(item) for item in (schema.get("required") or [])]
        missing = sorted(key for key in required if key not in value or value[key] is None)
        if missing:
            _schema_fail(path, f"{path}：缺少必需字段", {"missing_fields": missing})
        unexpected = sorted(str(key) for key in value if key not in properties)
        if unexpected:
            _schema_fail(
                path,
                f"{path}：包含未声明字段，模型输出只接受 schema 声明的字段",
                {"unexpected_fields": unexpected},
            )
        validated: dict[str, Any] = {}
        for key, sub_schema in properties.items():
            if key not in value or value[key] is None:
                continue
            validated[str(key)] = _validate_node(value[key], sub_schema, path=f"{path}.{key}")
        return validated

    if expected == "array":
        if not isinstance(value, list):
            _schema_fail(path, f"{path}：应为数组", {"received_type": type(value).__name__})
        items = schema.get("items")
        if not isinstance(items, Mapping):
            _schema_fail(path, f"{path}：schema items 无效")
        if len(value) > MAX_MODEL_ARRAY_ITEMS:
            _schema_fail(
                path,
                f"{path}：数组长度超过上限 {MAX_MODEL_ARRAY_ITEMS}",
                {"length": len(value), "limit": MAX_MODEL_ARRAY_ITEMS},
            )
        return [_validate_node(item, items, path=f"{path}[{index}]") for index, item in enumerate(value)]

    if expected == "string":
        if not isinstance(value, str):
            _schema_fail(path, f"{path}：应为字符串", {"received_type": type(value).__name__})
        choices = schema.get("enum")
        if isinstance(choices, (list, tuple)) and value not in choices:
            _schema_fail(path, f"{path}：取值不在允许范围内", {"value": value, "allowed": list(choices)})
        return value

    if expected == "boolean":
        if not isinstance(value, bool):
            _schema_fail(path, f"{path}：应为布尔值", {"received_type": type(value).__name__})
        return value

    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            _schema_fail(path, f"{path}：应为整数", {"received_type": type(value).__name__})
        return value

    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _schema_fail(path, f"{path}：应为数字", {"received_type": type(value).__name__})
        return value

    if expected == "null":
        if value is not None:
            _schema_fail(path, f"{path}：应为 null")
        return None

    _schema_fail(path, f"{path}：不支持的 schema 类型 {expected!r}")


def validate_model_payload(payload: Mapping[str, Any], schema: Mapping[str, Any], *, scope: str) -> dict[str, Any]:
    """Validate a model response against *schema* and return only declared fields.

    The returned copy contains exactly the schema-declared keys, so downstream
    code cannot accidentally read a hallucinated field name even if the schema is
    later loosened.
    """

    if not isinstance(payload, Mapping):
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{scope}：模型响应必须是对象", {"received_type": type(payload).__name__}
        )
    validated = _validate_node(dict(payload), schema, path=scope)
    if not isinstance(validated, dict):
        raise ExplainerContractError("SCHEMA_INVALID", f"{scope}：模型响应根节点必须是对象")
    return validated


def compose_extraction_request(
    *, system_instructions: str, untrusted_source_text: str, max_source_chars: int = 200_000
) -> dict[str, Any]:
    """Build a request that keeps trusted instructions and source text apart.

    The two strings are separate parameters and stay separate fields in the
    result; the source text is truncated to a bounded window and never merged,
    prefixed or interpolated into the system instruction.  ``injection_markers``
    is attached for the operator, not as an instruction to the pipeline.
    """

    if not (system_instructions or "").strip():
        raise ExplainerContractError("SCHEMA_INVALID", "系统指令不能为空")
    if max_source_chars <= 0:
        raise ExplainerContractError("SCHEMA_INVALID", "来源文本窗口必须是正整数", {"max_source_chars": max_source_chars})
    text = untrusted_source_text or ""
    window = text[:max_source_chars]
    return {
        "response_schema": FACT_EXTRACTION_SCHEMA,
        "system": system_instructions,
        "user_content": {
            "content_role": "UNTRUSTED_SOURCE_DATA",
            "text": window,
            "truncated": len(text) > max_source_chars,
            "injection_markers": looks_like_injection(window),
        },
        "boundary": {
            "source_text_is_data_only": True,
            "instructions_from_source_text_are_never_followed": True,
            "accepted_response_fields": "ONLY_DECLARED_SCHEMA_FIELDS",
            "extra_response_fields_are_rejected": True,
        },
    }


def _title_from_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").strip()
    path = (parsed.path or "").strip("/")
    segment = unquote(path.rsplit("/", 1)[-1]) if path else ""
    if segment:
        stem = segment.rsplit(".", 1)[0] if "." in segment else segment
        stem = re.sub(r"[-_+]+", " ", stem).strip()
        if len(stem) >= 2 and not stem.isdigit():
            return f"{stem} — {host}"[:300]
    return (host or url)[:300]


def _require_enum_value(enum_type: type[StrEnum], value: Any, field: str) -> str:
    try:
        return enum_type(str(value)).value
    except ValueError:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"{field} 取值不合法：{value!r}",
            {field: value, "allowed": [item.value for item in enum_type]},
        ) from None


def _require_allowed_value(allowed: Sequence[str], value: Any, field: str) -> str:
    """Validate a value against a plain allow-list (for CHECK-constrained
    columns that have no StrEnum, e.g. ``explainer_sources.source_kind``)."""

    text = str(value or "").strip()
    if text not in allowed:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"{field} 取值不合法：{value!r}",
            {field: value, "allowed": list(allowed)},
        )
    return text


def _derive_claim_status(*, stances: set[str], current_status: str | None) -> str:
    """Derive claim status from evidence stances — never from a count.

    * supports **and** refutes -> ``DISPUTED`` (the sources disagree; agreement
      is not a majority vote, so three copies of one wire story cannot outvote a
      primary document)
    * refutes only -> ``DISPUTED`` (the claim itself is contradicted)
    * supports only -> ``SUPPORTED``
    * no usable evidence -> ``UNVERIFIED``
    * an existing ``EXCLUDED`` stays excluded: that is a recorded human/policy
      decision and machine evidence must not silently reverse it.
    """

    if str(current_status or "") == ClaimStatus.EXCLUDED.value:
        return ClaimStatus.EXCLUDED.value
    has_support = EvidenceStance.SUPPORTS.value in stances
    has_refute = EvidenceStance.REFUTES.value in stances
    if has_support and has_refute:
        return ClaimStatus.DISPUTED.value
    if has_refute:
        return ClaimStatus.DISPUTED.value
    if has_support:
        return ClaimStatus.SUPPORTED.value
    return ClaimStatus.UNVERIFIED.value


class ExplainerResearchService:
    """Research packets, sources, spans, claims and the §6.3 content gate."""

    def __init__(self, repo: ExplainerRepository, *, fetcher: ControlledFetcher | None = None) -> None:
        self.repo = repo
        self.fetcher = fetcher

    # ------------------------------------------------------------------ packets
    def create_packet(
        self,
        *,
        project_id: str,
        video_id: str,
        mode: str,
        topic: str,
        allowed_domains: Sequence[str] = (),
        max_external_requests: int = 0,
    ) -> dict[str, Any]:
        """Create a new research packet revision for a video.

        The packet hash commits video, revision, mode, topic, allow-list and
        request budget, so a later change to any of them produces a different
        packet instead of silently reusing evidence gathered under other rules.
        """

        self.repo.require_explainer_project(project_id)
        video = self.repo.require_video_for_project(project_id)
        if str(video["id"]) != video_id:
            raise ExplainerContractError(
                "NOT_FOUND",
                "该解说作品不属于当前项目",
                {"project_id": project_id, "video_id": video_id, "actual_video_id": video["id"]},
            )
        resolved_mode = _require_enum_value(ResearchMode, mode, "mode")
        topic_text = (topic or "").strip()
        if not topic_text:
            raise ExplainerContractError("SCHEMA_INVALID", "研究题目不能为空", {"video_id": video_id})
        domains = normalise_domain_list(allowed_domains)
        budget = int(max_external_requests)
        if resolved_mode == ResearchMode.OFFLINE_IMPORT.value:
            if budget != 0:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "OFFLINE_IMPORT 模式的外发请求上限必须为 0",
                    {"mode": resolved_mode, "max_external_requests": budget},
                )
        elif budget <= 0:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "WEB_RESEARCH 模式必须声明正的外发请求上限",
                {"mode": resolved_mode, "max_external_requests": budget},
            )

        previous = self._latest_packet(video_id)
        revision_no = int(previous["revision_no"]) + 1 if previous else 1
        packet_hash = content_hash(
            {
                "schema_version": SCHEMA_VERSION,
                "video_id": video_id,
                "revision_no": revision_no,
                "mode": resolved_mode,
                "topic": topic_text,
                "allowed_domains": domains,
                "max_external_requests": budget,
            }
        )
        packet = self.repo.insert(
            "explainer_research_packets",
            {
                "video_id": video_id,
                "revision_no": revision_no,
                "status": "DRAFT",
                "mode": resolved_mode,
                "topic": topic_text,
                "allowed_domains_json": domains,
                "external_request_count": 0,
                "max_external_requests": budget,
                "content_hash": packet_hash,
                "blockers_json": [],
            },
        )
        superseded: list[dict[str, Any]] = []
        if previous is not None:
            superseded = self.repo.mark_dependents_stale(
                upstream_kind=UPSTREAM_KIND_RESEARCH_PACKET,
                upstream_id=str(previous["id"]),
                downstream_kinds=DEPENDENT_KINDS_OF_RESEARCH_PACKET,
                reason="SUPERSEDED_BY_NEW_RESEARCH_PACKET",
                invalidated_by=str(packet["id"]),
            )
        return {
            **packet,
            "allowed_domains": domains,
            "previous_packet_id": str(previous["id"]) if previous else None,
            "dependents_marked_stale": [str(item.get("id")) for item in superseded],
        }

    def record_external_request(self, *, packet_id: str) -> dict[str, Any]:
        """Count one outbound request against the packet's authorized budget.

        The counter is incremented *before* the request is made, so a crash or
        timeout cannot be used to obtain unlimited free attempts.
        """

        packet = self.repo.get("explainer_research_packets", packet_id)
        limit = int(packet.get("max_external_requests") or 0)
        used = int(packet.get("external_request_count") or 0)
        if used + 1 > limit:
            raise ExplainerContractError(
                ExplainerErrorCode.BUDGET_EXCEEDED.value,
                "该资料包的外发请求预算已用尽，已停止而不是继续联网",
                {
                    "packet_id": packet_id,
                    "max_external_requests": limit,
                    "external_request_count": used,
                    "requested": 1,
                },
            )
        self.repo.bump("explainer_research_packets", packet_id, external_request_count=1)
        updated = self.repo.get("explainer_research_packets", packet_id)
        return {
            "packet_id": packet_id,
            "external_request_count": int(updated.get("external_request_count") or 0),
            "max_external_requests": limit,
            "remaining": max(0, limit - int(updated.get("external_request_count") or 0)),
        }

    # ------------------------------------------------------------------ sources
    def import_document(
        self,
        *,
        project_id: str,
        video_id: str,
        packet_id: str,
        text: str,
        title: str,
        source_kind: str = "DOCUMENT_IMPORT",
        original_name: str = "",
        language: str | None = None,
        rel_path: str | None = None,
        rights: Mapping[str, Any] | None = None,
        retrieved_via: str = "OFFLINE_IMPORT",
        import_session_id: str | None = None,
        source_document_version_id: str | None = None,
    ) -> dict[str, Any]:
        """Record an already-decoded document as a source plus its spans.

        The API layer performs upload and format parsing; this method receives
        text only, which keeps file-system access out of the domain service.  The
        body is normalised with
        :func:`~local_drama.application.explainers.sources.normalise_document_text`
        and the span offsets refer to that normalised string — a caller that
        persists the body must persist exactly that value.

        ``original_name`` has no column in ``explainer_sources``; it is used only
        as a title fallback so an unnamed upload is still identifiable.
        """

        packet = self._require_packet(packet_id=packet_id, project_id=project_id, video_id=video_id)
        kind = _require_allowed_value(_SOURCE_KIND_VALUES, source_kind, "source_kind")
        via = _require_enum_value(RetrievalVia, retrieved_via, "retrieved_via")
        body = normalise_document_text(text or "")
        if not body:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "导入的文档正文为空，无法作为来源", {"packet_id": packet_id}
            )
        locale = normalize_locale(language) if language else None
        rights_map = dict(rights or {})
        credibility = classify_source_credibility(None, declared=str(rights_map.get("credibility_kind") or "") or None)
        resolved_title = (title or original_name or "未命名文档").strip()[:300]

        source = self.repo.insert(
            "explainer_sources",
            {
                "packet_id": packet_id,
                "video_id": video_id,
                "project_id": project_id,
                "source_kind": kind,
                "url": None,
                "title": resolved_title,
                "author_or_publisher": None,
                "published_at": None,
                "updated_at_source": None,
                "event_date": None,
                "event_date_precision": DatePrecision.UNKNOWN.value,
                "fetched_at": utc_now_iso(),
                "language": locale,
                "body_sha256": text_hash(body),
                "rel_path": rel_path,
                "byte_size": len(body.encode("utf-8")),
                "credibility_kind": credibility,
                "rights_json": rights_map,
                "upstream_source_id": None,
                "import_session_id": import_session_id,
                "source_document_version_id": source_document_version_id,
                "retrieved_via": via,
            },
        )
        spans = self._insert_spans(packet_id=packet_id, source_id=str(source["id"]), body=body)
        duplicate = self._duplicate_source(packet_id=packet_id, body_sha256=str(source["body_sha256"]), source_id=str(source["id"]))
        return {
            **source,
            "spans": spans,
            "span_count": len(spans),
            "duplicate_of_source_id": duplicate,
            "packet_revision_no": int(packet["revision_no"]),
        }

    def register_url_source(
        self,
        *,
        project_id: str,
        video_id: str,
        packet_id: str,
        url: str,
        mode: str,
        allowed_domains: Sequence[str] = (),
        published_at: str | None = None,
        updated_at_source: str | None = None,
        credibility_kind: str = "UNKNOWN",
        upstream_source_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a public URL under the SSRF policy and record it as a source.

        Offline packets (and offline callers) are refused *before* any network
        access, which is what makes ``OFFLINE_IMPORT`` a structural guarantee
        rather than a promise.  A republication must declare
        ``upstream_source_id`` pointing at the source it copies: three copies of
        one wire story must never look like three independent proofs.
        """

        packet = self._require_packet(packet_id=packet_id, project_id=project_id, video_id=video_id)
        requested_mode = _require_enum_value(ResearchMode, mode, "mode")
        if requested_mode == ResearchMode.OFFLINE_IMPORT.value or str(packet.get("mode")) == ResearchMode.OFFLINE_IMPORT.value:
            raise ExplainerContractError(
                ExplainerErrorCode.INVALID_REQUEST.value,
                "离线导入模式禁止联网研究，请改用已下载的文档或新建 WEB_RESEARCH 资料包",
                {"research_mode": mode, "packet_mode": packet.get("mode")},
            )
        url_text = (url or "").strip()
        if not url_text:
            raise ExplainerContractError("SCHEMA_INVALID", "来源链接不能为空", {"packet_id": packet_id})
        parsed = urlsplit(url_text)
        if (parsed.scheme or "").casefold() not in HTTP_SCHEMES or not parsed.hostname:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "只接受 http/https 的公开链接",
                {"url": url_text, "scheme": parsed.scheme},
            )
        host = parsed.hostname or ""
        effective_domains = normalise_domain_list(allowed_domains) or list(packet.get("allowed_domains_json") or [])
        if effective_domains and not domain_is_allowed(host, effective_domains):
            raise ExplainerContractError(
                ExplainerErrorCode.INVALID_REQUEST.value,
                "该域名不在资料包允许的来源域名列表内",
                {"url": url_text, "host": host, "allowed_domains": effective_domains},
            )
        credibility = _require_enum_value(CredibilityKind, credibility_kind, "credibility_kind")
        published_literal, published_precision = normalise_event_date(published_at)
        updated_literal, _updated_precision = normalise_event_date(updated_at_source)
        upstream_id: str | None = None
        if upstream_source_id:
            upstream = self.repo.find("explainer_sources", upstream_source_id)
            if upstream is None or str(upstream.get("packet_id")) != packet_id:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "转载来源必须指向同一资料包内已存在的原始来源",
                    {"upstream_source_id": upstream_source_id, "packet_id": packet_id},
                )
            upstream_id = str(upstream["id"])
        if self.fetcher is None:
            raise ExplainerContractError(
                ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                "未配置受控抓取器，无法联网获取来源",
                {"packet_id": packet_id, "mode": requested_mode},
            )

        self.record_external_request(packet_id=packet_id)
        fetched = self.fetcher.fetch(url_text)
        if fetched.get("status") == "DENIED":
            raise ExplainerContractError(
                ExplainerErrorCode.INVALID_REQUEST.value,
                f"该地址被出网策略拒绝：{fetched.get('denied_reason')}",
                {
                    "url": url_text,
                    "denied_reason": fetched.get("denied_reason"),
                    "classification": fetched.get("classification"),
                },
            )
        if fetched.get("status") != "OK":
            raise ExplainerContractError(
                ExplainerErrorCode.OUTPUT_VALIDATION_FAILED.value,
                f"来源抓取失败：{fetched.get('denied_reason') or fetched.get('status_code')}",
                {
                    "url": url_text,
                    "status": fetched.get("status"),
                    "status_code": fetched.get("status_code"),
                    "denied_reason": fetched.get("denied_reason"),
                },
            )

        body = normalise_document_text(str(fetched.get("body_text") or ""))
        if not body:
            raise ExplainerContractError(
                ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                "抓取到的页面没有可引用的正文，不能作为来源",
                {"url": url_text, "final_url": fetched.get("final_url"), "body_bytes": fetched.get("body_bytes")},
            )
        declared_credibility = credibility if credibility != CredibilityKind.UNKNOWN.value else None
        resolved_credibility = classify_source_credibility(url_text, declared=declared_credibility)
        source = self.repo.insert(
            "explainer_sources",
            {
                "packet_id": packet_id,
                "video_id": video_id,
                "project_id": project_id,
                "source_kind": "WEB_PAGE",
                "url": str(fetched.get("final_url") or url_text),
                "title": _title_from_url(url_text),
                "author_or_publisher": host,
                "published_at": published_literal,
                "updated_at_source": updated_literal,
                "event_date": None,
                "event_date_precision": DatePrecision.UNKNOWN.value,
                "fetched_at": utc_now_iso(),
                "language": None,
                "body_sha256": str(fetched.get("body_sha256") or text_hash(body)),
                "rel_path": None,
                "byte_size": int(fetched.get("body_bytes") or len(body.encode("utf-8"))),
                "credibility_kind": resolved_credibility,
                "rights_json": {},
                "upstream_source_id": upstream_id,
                "import_session_id": None,
                "source_document_version_id": None,
                "retrieved_via": RetrievalVia.WEB_RESEARCH.value,
            },
        )
        spans = self._insert_spans(packet_id=packet_id, source_id=str(source["id"]), body=body)
        return {
            **source,
            "spans": spans,
            "span_count": len(spans),
            "published_at_precision": published_precision,
            "fetch": {
                "status": fetched.get("status"),
                "status_code": fetched.get("status_code"),
                "final_url": fetched.get("final_url"),
                "redirect_chain": list(fetched.get("redirect_chain") or []),
                "content_type": fetched.get("content_type"),
                "classification": fetched.get("classification"),
            },
            "injection_markers": looks_like_injection(body),
            "content_is_untrusted_data": True,
        }

    # ------------------------------------------------------------------ facts
    def apply_fact_extraction(
        self,
        *,
        project_id: str,
        video_id: str,
        packet_id: str,
        extracted: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist a *validated* model fact-extraction payload.

        Only schema-declared fields are read (:data:`FACT_EXTRACTION_SCHEMA`); an
        undeclared key is rejected instead of ignored, because ignoring it would
        let a model shape persisted columns by name.  Evidence must reference a
        span of this packet, and ``independence_key`` is derived from
        ``upstream_source_id or source_id`` — never supplied by the model.
        ``verified_as_history`` is always written as ``False``: a model
        asserting a claim is not evidence that it happened, so the flag stays
        reserved for a later human/provenance decision.
        """

        packet = self._require_packet(packet_id=packet_id, project_id=project_id, video_id=video_id)
        video = self.repo.get("explainer_videos", video_id)
        is_fiction = str(video.get("content_kind")) == ContentKind.ORIGINAL_FICTION.value
        payload = validate_model_payload(extracted, FACT_EXTRACTION_SCHEMA, scope="fact_extraction")

        claims_result: list[dict[str, Any]] = []
        evidence_created = 0
        evidence_skipped = 0
        claim_index: dict[str, str] = {}

        for claim_payload in payload.get("claims") or []:
            code = str(claim_payload["code"]).strip()
            statement = str(claim_payload["statement"]).strip()
            if not code or not statement:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "事实断言的 code 与 statement 不能为空", {"code": code}
                )
            statement_kind = str(claim_payload.get("statement_kind") or StatementType.FACT.value)
            importance = str(claim_payload.get("importance") or "KEY")
            existing = self.repo.claim_by_code(video_id, code)

            stances: set[str] = set()
            pending_evidence: list[dict[str, Any]] = []
            for evidence in claim_payload.get("evidence") or []:
                span_id = str(evidence["source_span_id"]).strip()
                span = self.repo.find("explainer_source_spans", span_id)
                if span is None or str(span.get("packet_id")) != packet_id:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "证据必须引用本资料包内已存在的来源片段",
                        {"claim_code": code, "source_span_id": span_id, "packet_id": packet_id},
                    )
                declared_source_id = evidence.get("source_id")
                if declared_source_id and str(declared_source_id) != str(span["source_id"]):
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "证据声明的来源与来源片段不一致",
                        {
                            "claim_code": code,
                            "source_span_id": span_id,
                            "declared_source_id": declared_source_id,
                            "span_source_id": span["source_id"],
                        },
                    )
                source = self.repo.get("explainer_sources", str(span["source_id"]))
                stance = str(evidence["stance"])
                stances.add(stance)
                pending_evidence.append(
                    {
                        "source_id": str(source["id"]),
                        "source_span_id": span_id,
                        "stance": stance,
                        # Derived, never model-declared: a republication points at
                        # its upstream so copies collapse into one proof.
                        "independence_key": str(source.get("upstream_source_id") or source["id"]),
                        "note": str(evidence.get("note") or ""),
                    }
                )

            status = _derive_claim_status(stances=stances, current_status=str(existing.get("status")) if existing else None)
            verification = {
                "rule": "STANCE_NOT_VOTE",
                "evidence_submitted": len(pending_evidence),
                "stances": sorted(stances),
                "verified_as_history_forced_false": True,
            }
            fields: dict[str, Any] = {
                "video_id": video_id,
                "code": code,
                "statement": statement,
                "statement_kind": statement_kind,
                "status": status,
                "importance": importance,
                "confidence_reason": str(claim_payload.get("confidence_reason") or ""),
                "verified_as_history": False,
                "disambiguation_json": {},
                "verification_json": verification,
            }
            if existing and str(existing.get("packet_id") or "") not in {"", packet_id}:
                # Keep the original provenance packet; reusing the claim must not
                # rewrite which packet produced it.
                fields["packet_id"] = str(existing["packet_id"])
            else:
                fields["packet_id"] = packet_id

            if existing:
                claim = self.repo.update("explainer_claims", str(existing["id"]), fields)
            else:
                claim = self.repo.insert("explainer_claims", fields)
            claim_id = str(claim["id"])
            claim_index[code] = claim_id

            created_here = 0
            for evidence in pending_evidence:
                duplicate = self.repo.list_where(
                    "claim_evidence",
                    {
                        "claim_id": claim_id,
                        "source_span_id": evidence["source_span_id"],
                        "stance": evidence["stance"],
                    },
                )
                if duplicate:
                    evidence_skipped += 1
                    continue
                self.repo.insert("claim_evidence", {"claim_id": claim_id, **evidence})
                created_here += 1
                evidence_created += 1
            claims_result.append(
                {
                    "id": claim_id,
                    "code": code,
                    "status": status,
                    "importance": importance,
                    "statement_kind": statement_kind,
                    "reused_existing_claim": bool(existing),
                    "evidence_created": created_here,
                }
            )

        entities_result = self._apply_entities(video_id=video_id, project_id=project_id, payload=payload, is_fiction=is_fiction)
        entity_index = {item["code"]: item["id"] for item in entities_result}
        events_result = self._apply_events(video_id=video_id, payload=payload, claim_index=claim_index, entity_index=entity_index)

        packet_hash = str(packet.get("content_hash") or "")
        return {
            "video_id": video_id,
            "packet_id": packet_id,
            "packet_content_hash": packet_hash,
            "content_kind": str(video.get("content_kind") or ContentKind.FACTUAL_EXPLAINER.value),
            "claims": claims_result,
            "events": events_result,
            "entities": entities_result,
            "evidence_created": evidence_created,
            "evidence_skipped_duplicates": evidence_skipped,
            "claims_created": sum(1 for item in claims_result if not item["reused_existing_claim"]),
            "claims_reused": sum(1 for item in claims_result if item["reused_existing_claim"]),
            "accepted_fields": "ONLY_DECLARED_SCHEMA_FIELDS",
            "source_text_executed": False,
        }

    def reconcile_claims(self, *, video_id: str) -> dict[str, Any]:
        """Recompute every claim status from its recorded evidence stances.

        Disagreement becomes ``DISPUTED``; evidence counts only ever *order* the
        result (``confidence_reason``) and never decide truth.  Core conflicts
        and unsupported core claims are returned so the caller can route them to
        a human without re-querying.
        """

        claims = self._claims_for_video(video_id)
        results: list[dict[str, Any]] = []
        for claim in claims:
            claim_id = str(claim["id"])
            evidence = self.repo.claim_evidence(claim_id)
            stances = {str(item.get("stance")) for item in evidence}
            previous = str(claim.get("status") or ClaimStatus.UNVERIFIED.value)
            status = _derive_claim_status(stances=stances, current_status=previous)
            independent_groups = self.repo.independent_evidence_count(claim_id)
            supports = sum(1 for item in evidence if item.get("stance") == EvidenceStance.SUPPORTS.value)
            refutes = sum(1 for item in evidence if item.get("stance") == EvidenceStance.REFUTES.value)
            contexts = sum(1 for item in evidence if item.get("stance") == EvidenceStance.CONTEXT.value)
            confidence_reason = (
                f"独立证据组 {independent_groups} 个；支持 {supports} 条、反驳 {refutes} 条、背景 {contexts} 条；"
                "数量只用于排序，不作为真伪判定。"
            )
            verification = {
                "rule": "STANCE_NOT_VOTE",
                "independent_evidence_groups": independent_groups,
                "stance_counts": {
                    EvidenceStance.SUPPORTS.value: supports,
                    EvidenceStance.REFUTES.value: refutes,
                    EvidenceStance.CONTEXT.value: contexts,
                },
                "counts_do_not_establish_truth": True,
                "previous_status": previous,
                "human_exclusion_preserved": previous == ClaimStatus.EXCLUDED.value,
            }
            if status != previous or confidence_reason != str(claim.get("confidence_reason") or ""):
                self.repo.update(
                    "explainer_claims",
                    claim_id,
                    {"status": status, "confidence_reason": confidence_reason, "verification_json": verification},
                )
            results.append(
                {
                    "id": claim_id,
                    "code": str(claim.get("code")),
                    "importance": str(claim.get("importance")),
                    "status_before": previous,
                    "status": status,
                    "evidence_count": len(evidence),
                    "independent_evidence_groups": independent_groups,
                    "confidence_reason": confidence_reason,
                }
            )
        return {
            "video_id": video_id,
            "claims": results,
            "core_conflicts": [self._claim_projection(item) for item in self.repo.open_core_conflicts(video_id)],
            "unsupported_core": [self._claim_projection(item) for item in self.repo.unsupported_core_claims(video_id)],
            "rule": "STANCE_NOT_VOTE",
            "counts_do_not_establish_truth": True,
        }

    def content_gate(self, *, video_id: str, require_verified_scope: bool = False) -> dict[str, Any]:
        """The §6.3 auto-release gate.

        Blocking rules (each returned with ``code``/``message``/``scope``/
        ``next_step`` so the UI never has to invent guidance):

        * a ``DISPUTED`` claim of importance ``CORE`` or ``KEY`` -> ``CLAIM_CONFLICT``
        * an unsupported ``CORE`` factual claim -> ``SOURCE_EVIDENCE_MISSING``
        * a factual explainer with no recorded source at all -> ``SOURCE_EVIDENCE_MISSING``
        * ``require_verified_scope`` and a source without a verified, globally
          usable license -> ``LICENSE_SCOPE_UNVERIFIED``

        ``ORIGINAL_FICTION`` is not exempt: when an internal contradiction is
        flagged (claim status ``DISPUTED``) the gate blocks too, because a
        self-contradicting story is a defect regardless of factuality.  Fiction
        is however exempt from factual-support rules — invented content needs no
        citations.
        """

        video = self.repo.get("explainer_videos", video_id)
        content_kind = str(video.get("content_kind") or ContentKind.FACTUAL_EXPLAINER.value)
        is_fiction = content_kind == ContentKind.ORIGINAL_FICTION.value
        claims = self._claims_for_video(video_id)
        blockers: list[dict[str, Any]] = []

        for claim in claims:
            code = str(claim.get("code"))
            importance = str(claim.get("importance"))
            status = str(claim.get("status"))
            scope = f"CLAIM:{code}"
            if status == ClaimStatus.DISPUTED.value:
                if is_fiction or importance in CLAIM_IMPORTANCE_BLOCKING:
                    blockers.append(
                        self._blocker(
                            ExplainerErrorCode.CLAIM_CONFLICT.value,
                            f"断言《{code}》存在相互矛盾的来源或内部矛盾，未解决前不能自动发布。",
                            scope,
                            {"claim_code": code, "importance": importance, "content_kind": content_kind},
                        )
                    )
                continue
            if not is_fiction and importance == "CORE" and status in {
                ClaimStatus.UNVERIFIED.value,
                ClaimStatus.EXCLUDED.value,
            }:
                blockers.append(
                    self._blocker(
                        ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                        f"核心断言《{code}》没有可定位的支持证据，未解决前不能自动发布。",
                        scope,
                        {"claim_code": code, "status": status},
                    )
                )

        sources = self._sources_for_video(video_id)
        if not is_fiction and not sources:
            blockers.append(
                self._blocker(
                    ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                    "纪实解说没有任何来源记录，无法核验内容，不能自动发布。",
                    "VIDEO",
                    {"video_id": video_id, "source_count": 0},
                )
            )

        if require_verified_scope and sources:
            blockers.extend(self._license_blockers(sources))

        categories: dict[str, list[dict[str, Any]]] = {category: [] for category in PREFLIGHT_CATEGORIES}
        for blocker in blockers:
            categories.setdefault(classify_blocker(blocker["code"]), []).append(blocker)
        status_value = "BLOCKED" if blockers else "PASS"
        return {
            "video_id": video_id,
            "status": status_value,
            "executable": not blockers,
            "blockers": blockers,
            "categories": categories,
            "gate": CONTENT_GATE_RULE_VERSION,
            "section": "6.3_AUTO_RELEASE",
            "content_kind": content_kind,
            "require_verified_scope": bool(require_verified_scope),
            "checked_at": utc_now_iso(),
            "machine_pass_is_not_publication_authorization": True,
            "human_approval_required_for_publication": True,
        }

    def summarise_sources(self, *, packet_id: str) -> dict[str, Any]:
        """Per-source summary plus independence clustering.

        Sources are grouped by ``upstream_source_id or id``.  Reprints of one wire
        story therefore land in one group: the summary reports
        ``independent_evidence_groups`` and states plainly that counts do not
        establish truth, so no screen can present "5 sources agree" as proof.
        """

        packet = self.repo.get("explainer_research_packets", packet_id)
        sources = self._sources_for_packet(packet_id)
        records: list[dict[str, Any]] = []
        groups: dict[str, dict[str, Any]] = {}
        for source in sources:
            source_id = str(source["id"])
            upstream_id = source.get("upstream_source_id")
            key = str(upstream_id or source_id)
            spans = self._spans_for_source(source_id)
            quoted = "\n".join(str(span.get("quote_text") or "") for span in spans)
            markers = looks_like_injection(quoted)
            upstream = self.repo.find("explainer_sources", upstream_id) if upstream_id else None
            records.append(
                {
                    "id": source_id,
                    "source_kind": source.get("source_kind"),
                    "url": source.get("url"),
                    "title": source.get("title"),
                    "author_or_publisher": source.get("author_or_publisher"),
                    "published_at": source.get("published_at"),
                    "updated_at_source": source.get("updated_at_source"),
                    "event_date": source.get("event_date"),
                    "event_date_precision": source.get("event_date_precision"),
                    "fetched_at": source.get("fetched_at"),
                    "language": source.get("language"),
                    "body_sha256": source.get("body_sha256"),
                    "rel_path": source.get("rel_path"),
                    "byte_size": source.get("byte_size"),
                    "credibility_kind": source.get("credibility_kind"),
                    "retrieved_via": source.get("retrieved_via"),
                    "independence_key": key,
                    "is_republication": bool(upstream_id),
                    "upstream_source_id": upstream_id,
                    "upstream_title": (upstream or {}).get("title"),
                    "span_count": len(spans),
                    "suspicious_markers": markers,
                }
            )
            group = groups.setdefault(
                key,
                {"independence_key": key, "source_ids": [], "titles": [], "is_republication_group": bool(upstream_id)},
            )
            group["source_ids"].append(source_id)
            group["titles"].append(str(source.get("title") or ""))
        ordered_groups = [groups[key] for key in sorted(groups)]
        return {
            "packet_id": packet_id,
            "packet_revision_no": int(packet.get("revision_no") or 0),
            "packet_mode": packet.get("mode"),
            "source_count": len(records),
            "sources": records,
            "groups": ordered_groups,
            "independent_evidence_groups": len(ordered_groups),
            "counts_do_not_establish_truth": True,
            "credibility_kind_is_a_hint": True,
            "suspicious_markers_are_reported_not_executed": True,
            "content_is_untrusted_data": True,
        }

    # ------------------------------------------------------------------ internal
    def _require_packet(self, *, packet_id: str, project_id: str, video_id: str) -> dict[str, Any]:
        packet = self.repo.get("explainer_research_packets", packet_id)
        if str(packet.get("video_id")) != video_id:
            raise ExplainerContractError(
                "NOT_FOUND",
                "资料包不属于该解说作品",
                {"packet_id": packet_id, "video_id": video_id, "packet_video_id": packet.get("video_id")},
            )
        video = self.repo.get("explainer_videos", video_id)
        if str(video.get("project_id")) != project_id:
            raise ExplainerContractError(
                "NOT_FOUND",
                "解说作品不属于该项目",
                {"video_id": video_id, "project_id": project_id, "video_project_id": video.get("project_id")},
            )
        return packet

    def _latest_packet(self, video_id: str) -> dict[str, Any] | None:
        rows = self.repo.list_where(
            "explainer_research_packets", {"video_id": video_id}, order_by="revision_no", descending=True, limit=1
        )
        return rows[0] if rows else None

    def _claims_for_video(self, video_id: str) -> list[dict[str, Any]]:
        rows = self.repo.list_where("explainer_claims", {"video_id": video_id}, order_by="code", descending=False)
        return sorted(rows, key=lambda row: str(row.get("code") or ""))

    def _sources_for_video(self, video_id: str) -> list[dict[str, Any]]:
        rows = self.repo.list_where("explainer_sources", {"video_id": video_id})
        return self._stable_source_order(rows)

    def _sources_for_packet(self, packet_id: str) -> list[dict[str, Any]]:
        rows = self.repo.list_where("explainer_sources", {"packet_id": packet_id})
        return self._stable_source_order(rows)

    @staticmethod
    def _stable_source_order(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Order by (created_at, id): ``created_at`` is second-resolution, so the
        id tiebreaker keeps two runs over the same database identical."""

        ordered = sorted(rows, key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")))
        return [dict(row) for row in ordered]

    def _spans_for_source(self, source_id: str) -> list[dict[str, Any]]:
        rows = self.repo.list_where("explainer_source_spans", {"source_id": source_id}, order_by="ordinal", descending=False)
        return [dict(row) for row in rows]

    def _duplicate_source(self, *, packet_id: str, body_sha256: str, source_id: str) -> str | None:
        for row in self._sources_for_packet(packet_id):
            if str(row.get("body_sha256")) == body_sha256 and str(row.get("id")) != source_id:
                return str(row["id"])
        return None

    def _insert_spans(self, *, packet_id: str, source_id: str, body: str) -> list[dict[str, Any]]:
        spans = split_paragraph_spans(body)
        if not spans:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "正文没有可切分的段落，无法建立可定位的证据片段", {"source_id": source_id}
            )
        rows: list[dict[str, Any]] = []
        for span in spans:
            rows.append(
                self.repo.insert(
                    "explainer_source_spans",
                    {
                        "source_id": source_id,
                        "packet_id": packet_id,
                        "ordinal": span["ordinal"],
                        "start_offset": span["start_offset"],
                        "end_offset": span["end_offset"],
                        "quote_text": span["quote_text"],
                        "span_hash": span["span_hash"],
                        "paragraph_no": span["paragraph_no"],
                        "page_no": None,
                    },
                )
            )
        return rows

    def _entity_by_code(self, video_id: str, code: str) -> dict[str, Any] | None:
        rows = self.repo.list_where("explainer_entities", {"video_id": video_id, "code": code})
        return rows[0] if rows else None

    def _resolve_entity_id(self, video_id: str, reference: str) -> str:
        entity = self._entity_by_code(video_id, reference)
        if entity is None:
            candidate = self.repo.find("explainer_entities", reference)
            if candidate is not None and str(candidate.get("video_id")) == video_id:
                entity = candidate
        if entity is None:
            raise ExplainerContractError(
                "SCHEMA_INVALID", f"引用了不存在的人物/实体：{reference}", {"video_id": video_id, "entity_ref": reference}
            )
        return str(entity["id"])

    def _resolve_claim_id(self, video_id: str, reference: str, claim_index: Mapping[str, str]) -> str:
        if reference in claim_index:
            return claim_index[reference]
        claim = self.repo.claim_by_code(video_id, reference)
        if claim is None:
            candidate = self.repo.find("explainer_claims", reference)
            if candidate is not None and str(candidate.get("video_id")) == video_id:
                claim = candidate
        if claim is None:
            raise ExplainerContractError(
                "SCHEMA_INVALID", f"引用了不存在的事实断言：{reference}", {"video_id": video_id, "claim_ref": reference}
            )
        return str(claim["id"])

    def _apply_entities(
        self, *, video_id: str, project_id: str, payload: Mapping[str, Any], is_fiction: bool
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for entity_payload in payload.get("entities") or []:
            code = str(entity_payload["code"]).strip()
            name = str(entity_payload["name"]).strip()
            if not code or not name:
                raise ExplainerContractError("SCHEMA_INVALID", "实体的 code 与 name 不能为空", {"code": code})
            entity_type = str(entity_payload["entity_type"])
            # Fiction keeps its characters marked fictional even if the model
            # omits the flag: an invented character must never be treated as a
            # real person downstream.
            fictional = is_fiction or entity_type == EntityType.FICTIONAL_CHARACTER.value
            fields: dict[str, Any] = {
                "video_id": video_id,
                "project_id": project_id,
                "code": code,
                "entity_type": entity_type,
                "name": name[:200],
                "latin_name": (str(entity_payload["latin_name"])[:200] if entity_payload.get("latin_name") else None),
                "aliases_json": [str(item) for item in (entity_payload.get("aliases") or [])],
                "fictional": fictional,
                "descriptive_only": bool(entity_payload.get("descriptive_only") or False),
                "disambiguation_json": {},
                "status": "ACTIVE",
            }
            existing = self._entity_by_code(video_id, code)
            if existing:
                entity = self.repo.update("explainer_entities", str(existing["id"]), fields)
            else:
                entity = self.repo.insert("explainer_entities", fields)
            entity_id = str(entity["id"])
            state = entity_payload.get("state")
            revision = self._apply_entity_state(video_id=video_id, entity_id=entity_id, state=state) if state else None
            if revision is not None:
                self.repo.update("explainer_entities", entity_id, {"canonical_state_revision_id": str(revision["id"])})
            results.append(
                {
                    "id": entity_id,
                    "code": code,
                    "entity_type": entity_type,
                    "fictional": fictional,
                    "reused_existing_entity": bool(existing),
                    "state_revision_id": str(revision["id"]) if revision else None,
                }
            )
        return results

    def _apply_entity_state(self, *, video_id: str, entity_id: str, state: Mapping[str, Any]) -> dict[str, Any] | None:
        carried: list[str] = []
        for reference in state.get("carried_prop_entity_codes") or []:
            carried.append(self._resolve_entity_id(video_id, str(reference)))
        valid_from, _from_precision = normalise_event_date(state.get("valid_from_story_time"))
        valid_to, _to_precision = normalise_event_date(state.get("valid_to_story_time"))
        age = state.get("age")
        if age is not None and not 0 <= int(age) <= 200:
            raise ExplainerContractError("SCHEMA_INVALID", "年龄必须在 0–200 之间", {"age": age, "entity_id": entity_id})
        state_hash = content_hash(
            {
                "label": str(state.get("label") or ""),
                "age": int(age) if age is not None else None,
                "wardrobe": str(state.get("wardrobe") or ""),
                "condition": str(state.get("condition") or ""),
                "carried_prop_entity_ids": carried,
                "valid_from_story_time": valid_from,
                "valid_to_story_time": valid_to,
            }
        )
        latest_rows = self.repo.list_where(
            "entity_state_revisions", {"entity_id": entity_id}, order_by="revision_no", descending=True, limit=1
        )
        latest = latest_rows[0] if latest_rows else None
        if latest is not None and str(latest.get("content_hash")) == state_hash:
            # Identical state: reusing the revision keeps the lineage a chain
            # instead of a pile of no-op revisions.
            return None
        revision_no = int(latest["revision_no"]) + 1 if latest else 1
        return self.repo.insert(
            "entity_state_revisions",
            {
                "entity_id": entity_id,
                "revision_no": revision_no,
                "label": str(state.get("label") or "")[:200],
                "age": int(age) if age is not None else None,
                "wardrobe": str(state.get("wardrobe") or ""),
                "condition": str(state.get("condition") or ""),
                "carried_prop_entity_ids_json": carried,
                "valid_from_story_time": valid_from,
                "valid_to_story_time": valid_to,
                "identity_pack_version_id": None,
                "reference_media_version_ids_json": [],
                "content_hash": state_hash,
                "source_state_id": str(latest["id"]) if latest else None,
            },
        )

    def _apply_events(
        self,
        *,
        video_id: str,
        payload: Mapping[str, Any],
        claim_index: Mapping[str, str],
        entity_index: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for event_payload in payload.get("events") or []:
            code = str(event_payload["code"]).strip()
            title = str(event_payload["title"]).strip()
            if not code or not title:
                raise ExplainerContractError("SCHEMA_INVALID", "事件的 code 与 title 不能为空", {"code": code})
            start_literal, start_precision = normalise_event_date(event_payload.get("story_time_start"))
            end_literal, end_precision = normalise_event_date(event_payload.get("story_time_end"))
            # Precision is derived from the literals actually recorded; the model
            # never gets to declare "exact minute" for a year-only source.
            precision = _story_time_precision(start_precision, end_precision)
            participants: list[str] = []
            for reference in event_payload.get("participant_entity_codes") or []:
                reference_text = str(reference)
                participants.append(entity_index.get(reference_text) or self._resolve_entity_id(video_id, reference_text))
            claim_ids: list[str] = []
            for reference in event_payload.get("claim_codes") or []:
                claim_ids.append(self._resolve_claim_id(video_id, str(reference), claim_index))
            place_label = event_payload.get("place_label")
            place_entity_id: str | None = None
            if place_label:
                labelled = str(place_label)
                for entity in self.repo.list_where("explainer_entities", {"video_id": video_id}):
                    if labelled in {str(entity.get("code")), str(entity.get("name"))}:
                        place_entity_id = str(entity["id"])
                        break
            fields: dict[str, Any] = {
                "video_id": video_id,
                "code": code,
                "title": title[:300],
                "story_time_start": start_literal,
                "story_time_end": end_literal,
                "story_time_precision": precision,
                "calendar_system": (str(event_payload["calendar_system"]) if event_payload.get("calendar_system") else None),
                "place_entity_id": place_entity_id,
                "place_label": (str(place_label)[:200] if place_label else None),
                "participant_entity_ids_json": participants,
                "claim_ids_json": claim_ids,
                "causal_note": str(event_payload.get("causal_note") or ""),
                "sequence_no": int(event_payload.get("sequence_no") or 0),
            }
            existing_rows = self.repo.list_where("explainer_events", {"video_id": video_id, "code": code})
            existing = existing_rows[0] if existing_rows else None
            if existing:
                event = self.repo.update("explainer_events", str(existing["id"]), fields)
            else:
                event = self.repo.insert("explainer_events", fields)
            results.append(
                {
                    "id": str(event["id"]),
                    "code": code,
                    "story_time_precision": precision,
                    "reused_existing_event": bool(existing),
                }
            )
        return results

    def _license_blockers(self, sources: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate each source's recorded rights for a global release.

        ``intended_territories`` is fixed to ``GLOBAL`` here because this gate
        answers "may an automated pipeline release this?"; a narrower release
        must be authorized explicitly elsewhere rather than assumed by default.
        """

        assets = [
            AssetLicense(
                asset_kind="SOURCE",
                asset_id=str(source["id"]),
                asset_label=str(source.get("title") or source["id"]),
                scope=str((source.get("rights_json") or {}).get("scope") or "UNKNOWN"),
                territories=tuple(str(item) for item in ((source.get("rights_json") or {}).get("territories") or ())),
                evidence_ref=(source.get("rights_json") or {}).get("evidence_ref"),
                note=str((source.get("rights_json") or {}).get("note") or ""),
            )
            for source in sources
        ]
        evaluation = evaluate_license_scope(assets=assets, intended_territories=("GLOBAL",), require_verified_scope=True)
        by_id = {asset.asset_id: asset for asset in assets}
        blockers: list[dict[str, Any]] = []
        for item in evaluation.blockers:
            asset_id = str(item.get("asset_id") or "")
            label = by_id[asset_id].asset_label if asset_id in by_id else asset_id
            blockers.append(
                self._blocker(
                    ExplainerErrorCode.LICENSE_SCOPE_UNVERIFIED.value,
                    f"来源《{label}》没有可用于全球发布的许可范围证据。",
                    f"SOURCE:{asset_id}",
                    dict(item),
                )
            )
        return blockers

    @staticmethod
    def _blocker(code: str, message: str, scope: str, details: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "code": code,
            "message": message,
            "scope": scope,
            "details": dict(details),
            "next_step": ERROR_NEXT_STEP.get(code, ""),
        }

    @staticmethod
    def _claim_projection(claim: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": str(claim.get("id")),
            "code": str(claim.get("code")),
            "statement": str(claim.get("statement") or ""),
            "status": str(claim.get("status")),
            "importance": str(claim.get("importance")),
            "packet_id": claim.get("packet_id"),
        }


#: Valid ``source_kind`` values, mirroring the ``ck_explainer_sources_kind``
#: check constraint in migration 0102.
_SOURCE_KIND_VALUES: tuple[str, ...] = (
    "DOCUMENT_IMPORT",
    "WEB_PAGE",
    "REFERENCE_LINK",
    "LICENSED_MEDIA",
    "AUTHORED_FICTION_PACK",
)


def _story_time_precision(first: str, second: str) -> str:
    """Precision of a recorded story-time interval.

    The coarsest *known* bound wins, because an interval cannot be more precise
    than its loosest end.  A missing bound (``UNKNOWN``) is ignored rather than
    propagated: an event recorded only as "1936" is still known to year
    resolution, and reporting ``UNKNOWN`` would throw that away.
    """

    ladder = [
        DatePrecision.YEAR.value,
        DatePrecision.MONTH.value,
        DatePrecision.DAY.value,
        DatePrecision.MINUTE.value,
        DatePrecision.SECOND.value,
    ]
    known = [value for value in (first, second) if value in ladder]
    if not known:
        return DatePrecision.UNKNOWN.value
    return ladder[min(ladder.index(value) for value in known)]
