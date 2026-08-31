"""SSRF-safe HTTP utilities for user-configured external model endpoints."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpcore
import httpx
from django.conf import settings


class ExternalEndpointError(ValueError):
    """Raised when an external endpoint is malformed or unsafe to contact."""


@dataclass(frozen=True)
class ResolvedEndpoint:
    """Validated endpoint metadata and the IPs allowed for its connections."""

    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    # is_global excludes loopback, link-local, private, multicast, reserved,
    # unspecified and documentation-only ranges. Explicit metadata addresses
    # remain listed for readability and defense in depth.
    metadata_addresses = {
        "100.100.100.200",  # Alibaba Cloud metadata
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "169.254.170.2",  # ECS task metadata
        "fd00:ec2::254",  # IPv6 EC2 metadata
    }
    return str(address) not in metadata_addresses and address.is_global


def _resolve_public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        addresses = (str(literal),)
    else:
        try:
            records = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise ExternalEndpointError("外部模型地址无法解析") from exc
        addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records))

    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise ExternalEndpointError("外部模型地址解析到了非公网地址")
    return addresses


def validate_external_endpoint(
    value: str,
    *,
    allow_insecure_http: bool | None = None,
    max_length: int = 512,
) -> ResolvedEndpoint:
    """Validate scheme, authority and DNS results before any outbound request."""
    raw = (value or "").strip()
    if not raw or len(raw) > max_length:
        raise ExternalEndpointError("外部模型地址格式无效")

    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ExternalEndpointError("外部模型地址格式无效") from exc

    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ExternalEndpointError("外部模型地址只支持 HTTP/HTTPS 且必须包含主机名")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ExternalEndpointError("外部模型地址不得包含凭据、查询参数或片段")
    if allow_insecure_http is None:
        allow_insecure_http = bool(getattr(settings, "ALLOW_INSECURE_EXTERNAL_HTTP", False))
    if parsed.scheme != "https" and not allow_insecure_http:
        raise ExternalEndpointError("生产默认只允许 HTTPS 外部模型地址")

    normalized_host = hostname.rstrip(".").lower()
    resolved_port = port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolve_public_addresses(normalized_host, resolved_port)
    return ResolvedEndpoint(
        url=raw,
        scheme=parsed.scheme,
        hostname=normalized_host,
        port=resolved_port,
        addresses=addresses,
    )


class _PinnedNetworkBackend(httpcore.SyncBackend):
    """Connect only to the IPs returned by the validation immediately before use."""

    def __init__(self, endpoint: ResolvedEndpoint):
        self._hostname = endpoint.hostname
        self._addresses = endpoint.addresses

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        if host.rstrip(".").lower() != self._hostname:
            raise httpcore.ConnectError("外部模型重定向目标未通过固定地址校验")
        last_error: Exception | None = None
        for address in self._addresses:
            try:
                return super().connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # try the next already-validated address
                last_error = exc
        assert last_error is not None
        raise last_error


class _LimitedResponseStream(httpx.SyncByteStream):
    def __init__(self, stream, max_bytes: int):
        self._stream = stream
        self._max_bytes = max_bytes
        self._read = 0

    def __iter__(self):
        for chunk in self._stream:
            self._read += len(chunk)
            if self._read > self._max_bytes:
                self.close()
                raise httpx.ReadError("外部模型响应超过大小限制")
            yield chunk

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if close is not None:
            close()


class _PinnedHTTPTransport(httpx.BaseTransport):
    def __init__(self, endpoint: ResolvedEndpoint, max_response_bytes: int):
        self._max_response_bytes = max_response_bytes
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpcore.default_ssl_context(),
            network_backend=_PinnedNetworkBackend(endpoint),
            max_connections=4,
            max_keepalive_connections=2,
            keepalive_expiry=5.0,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        response = self._pool.handle_request(core_request)
        content_length = dict(response.headers).get(b"content-length")
        if content_length is not None and int(content_length) > self._max_response_bytes:
            response.close()
            raise httpx.ReadError("外部模型响应超过大小限制")
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_LimitedResponseStream(response.stream, self._max_response_bytes),
            extensions=response.extensions,
            request=request,
        )

    def close(self) -> None:
        self._pool.close()


def create_safe_http_client(base_url: str) -> httpx.Client:
    """Create a no-proxy, no-redirect client pinned to validated public IPs."""
    endpoint = validate_external_endpoint(base_url)
    max_redirects = int(getattr(settings, "EXTERNAL_API_MAX_REDIRECTS", 0))
    if max_redirects != 0:
        raise ExternalEndpointError("外部模型重定向策略必须保持为 0")
    max_response_bytes = int(getattr(settings, "EXTERNAL_API_MAX_RESPONSE_BYTES", 1_048_576))
    if max_response_bytes <= 0:
        raise ExternalEndpointError("外部模型响应大小限制必须为正数")
    timeout_values = {
        "connect": float(getattr(settings, "EXTERNAL_API_CONNECT_TIMEOUT_SECONDS", 5)),
        "read": float(getattr(settings, "EXTERNAL_API_READ_TIMEOUT_SECONDS", 10)),
        "write": float(getattr(settings, "EXTERNAL_API_WRITE_TIMEOUT_SECONDS", 5)),
        "pool": float(getattr(settings, "EXTERNAL_API_POOL_TIMEOUT_SECONDS", 5)),
    }
    if any(value <= 0 for value in timeout_values.values()):
        raise ExternalEndpointError("外部模型超时配置必须为正数")

    def validate_redirect(response: httpx.Response) -> None:
        if response.is_redirect and response.headers.get("location"):
            target = response.url.join(response.headers["location"])
            validate_external_endpoint(str(target))

    timeout = httpx.Timeout(**timeout_values)
    return httpx.Client(
        transport=_PinnedHTTPTransport(endpoint, max_response_bytes),
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        event_hooks={"response": [validate_redirect]},
    )
