from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from django.test import SimpleTestCase, override_settings

from deepseek_project.external_endpoint import (
    ExternalEndpointError,
    ResolvedEndpoint,
    _LimitedResponseStream,
    _PinnedHTTPTransport,
    create_safe_http_client,
    validate_external_endpoint,
)


class ExternalEndpointSecurityTests(SimpleTestCase):
    def test_rejects_non_https_and_all_non_public_literal_addresses(self):
        with self.assertRaises(ExternalEndpointError):
            validate_external_endpoint("http://8.8.8.8/v1")

        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"):
            with self.subTest(address=address), self.assertRaises(ExternalEndpointError):
                validate_external_endpoint(
                    f"https://[{address}]/v1" if ":" in address else f"https://{address}/v1"
                )

    @override_settings(ALLOW_INSECURE_EXTERNAL_HTTP=True)
    def test_http_is_allowed_only_with_explicit_development_flag(self):
        endpoint = validate_external_endpoint("http://8.8.8.8/v1")

        self.assertEqual(endpoint.port, 80)
        self.assertEqual(endpoint.addresses, ("8.8.8.8",))

    def test_rejects_credentials_query_fragment_and_unsafe_dns_results(self):
        for value in (
            "https://user:password@8.8.8.8/v1",
            "https://8.8.8.8/v1?token=secret",
            "https://8.8.8.8/v1#fragment",
            "ftp://8.8.8.8/v1",
        ):
            with self.subTest(value=value), self.assertRaises(ExternalEndpointError):
                validate_external_endpoint(value)

        with patch(
            "deepseek_project.external_endpoint.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("8.8.8.8", 443)),
                (2, 1, 6, "", ("192.168.1.10", 443)),
            ],
        ):
            with self.assertRaises(ExternalEndpointError):
                validate_external_endpoint("https://provider.example/v1")

    def test_safe_client_disables_proxy_and_redirect_following(self):
        client = create_safe_http_client("https://8.8.8.8/v1")
        try:
            self.assertFalse(client._trust_env)
            self.assertFalse(client.follow_redirects)
        finally:
            client.close()

    def test_redirect_target_is_validated_and_not_followed(self):
        client = create_safe_http_client("https://8.8.8.8/v1")
        calls = []
        client._transport = httpx.MockTransport(
            lambda request: (
                calls.append(request),
                httpx.Response(302, headers={"location": "http://127.0.0.1/private"}),
            )[1]
        )
        try:
            with self.assertRaises(ExternalEndpointError):
                client.get("https://8.8.8.8/v1")
            self.assertEqual(len(calls), 1)
        finally:
            client.close()

    def test_response_stream_enforces_maximum_size(self):
        stream = _LimitedResponseStream(iter([b"123", b"456"]), max_bytes=5)

        with self.assertRaises(httpx.ReadError):
            list(stream)

    def test_transport_accepts_httpcore_header_list(self):
        endpoint = ResolvedEndpoint(
            url="https://provider.example/v1",
            scheme="https",
            hostname="provider.example",
            port=443,
            addresses=("8.8.8.8",),
        )
        transport = _PinnedHTTPTransport(endpoint, max_response_bytes=1024)
        core_response = SimpleNamespace(
            status=200,
            headers=[
                (b"content-length", b"2"),
                (b"content-type", b"application/json"),
            ],
            stream=iter([b"{}"]),
            extensions={},
            close=Mock(),
        )
        transport._pool.handle_request = Mock(return_value=core_response)

        try:
            response = transport.handle_request(
                httpx.Request("GET", "https://provider.example/v1/models")
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.read(), b"{}")
        finally:
            transport.close()

    def test_transport_rejects_declared_oversized_response(self):
        endpoint = ResolvedEndpoint(
            url="https://provider.example/v1",
            scheme="https",
            hostname="provider.example",
            port=443,
            addresses=("8.8.8.8",),
        )
        transport = _PinnedHTTPTransport(endpoint, max_response_bytes=1)
        core_response = SimpleNamespace(
            status=200,
            headers=[(b"content-length", b"2")],
            stream=iter([b"{}"]),
            extensions={},
            close=Mock(),
        )
        transport._pool.handle_request = Mock(return_value=core_response)

        try:
            with self.assertRaises(httpx.ReadError):
                transport.handle_request(httpx.Request("GET", "https://provider.example/v1/models"))
            core_response.close.assert_called_once_with()
        finally:
            transport.close()
