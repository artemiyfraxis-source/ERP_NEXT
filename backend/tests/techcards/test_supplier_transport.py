"""Supplier-site client (audit U09): the checked IP is the one connected to, redirects are checked again, certificates
are verified except for the listed domains, and a page read without the check says so.

No network: the httpcore backend under the pinned one is a fake that records where it was asked to connect and plays
back canned HTTP answers."""

import socket

import httpcore
import pytest

from app.modules.techcards.kitset_names import (
    HttpPageFetcher,
    PinnedPublicBackend,
    SupplierSiteTransport,
    fetch_page,
    public_addresses,
    supplier_client,
    tls_exception_hosts,
    tls_unverified,
)

PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "151.101.1.1"


def _answer(body: str, status: str = "200 OK", headers: tuple[str, ...] = ()) -> list[bytes]:
    payload = body.encode()
    head = [f"HTTP/1.1 {status}", "Content-Type: text/html; charset=utf-8", f"Content-Length: {len(payload)}", *headers]
    return [("\r\n".join(head) + "\r\n\r\n").encode() + payload]


class RecordingStream(httpcore.MockStream):
    def __init__(self, buffer, tls_names):
        super().__init__(buffer)
        self._tls_names = tls_names

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self._tls_names.append((server_hostname, ssl_context.verify_mode))
        return self


class FakeNetwork(httpcore.NetworkBackend):
    """Each connect plays the next canned answer and records (address, port)."""

    def __init__(self, *answers: list[bytes]):
        self.answers = list(answers)
        self.connected: list[tuple[str, int]] = []
        self.tls: list[tuple[str | None, object]] = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append((host, port))
        if not self.answers:
            raise httpcore.ConnectError("no answer")
        return RecordingStream(list(self.answers.pop(0)), self.tls)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):  # pragma: no cover
        raise AssertionError("unix socket")

    def sleep(self, seconds):  # pragma: no cover
        pass


class SequenceResolver:
    """A DNS that answers differently on each lookup — the rebinding attack in miniature."""

    def __init__(self, *answers: list[str]):
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, host, port):
        self.asked.append(host)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


@pytest.fixture()
def hook_dns(monkeypatch):
    """The early hook's own lookup (socket.getaddrinfo) — set per test to model what the hook sees."""

    def install(address: str):
        monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a, **k: [(2, 1, 6, "", (address, 0))])

    install(PUBLIC)
    return install


def _fetcher(network: FakeNetwork, resolver, exceptions: tuple[str, ...] = ()) -> HttpPageFetcher:
    transport = SupplierSiteTransport(resolver=resolver, backend=network, exceptions=exceptions)
    return HttpPageFetcher(supplier_client(transport))


# ── DNS pinning ──


def test_the_checked_address_is_the_one_connected_to(hook_dns):
    network = FakeNetwork(_answer("<title>Провод МГТФ</title>"))
    resolver = SequenceResolver([PUBLIC])

    assert _fetcher(network, resolver).title("http://shop.example/p") == "Провод МГТФ"
    assert network.connected == [(PUBLIC, 80)]  # the IP, not the name: no second lookup at connect time


def test_dns_rebinding_after_the_check_never_reaches_an_internal_address(hook_dns):
    """The hook's lookup says public, the lookup the connection uses says 127.0.0.1: nothing is connected."""
    network = FakeNetwork(_answer("<title>secret</title>"))
    resolver = SequenceResolver(["127.0.0.1"])

    assert _fetcher(network, resolver).title("http://rebind.example/") == ""
    assert network.connected == []


def test_one_internal_address_among_public_ones_refuses_the_host():
    with pytest.raises(httpcore.ConnectError, match="not public"):
        public_addresses("mixed.example", 443, lambda host, port: [PUBLIC, "10.0.0.5"])
    with pytest.raises(httpcore.ConnectError, match="not public"):
        public_addresses("mapped.example", 443, lambda host, port: ["::ffff:169.254.169.254"])
    with pytest.raises(httpcore.ConnectError, match="does not resolve"):
        public_addresses("void.example", 443, lambda host, port: [])
    assert public_addresses("ok.example", 443, lambda host, port: [PUBLIC, PUBLIC, OTHER_PUBLIC]) == [PUBLIC, OTHER_PUBLIC]


def test_a_failed_first_address_falls_through_to_the_next_checked_one():
    class FirstDown(FakeNetwork):
        def connect_tcp(self, host, port, **kwargs):
            if host == PUBLIC:
                self.connected.append((host, port))
                raise httpcore.ConnectError("down")
            return super().connect_tcp(host, port, **kwargs)

    network = FirstDown(_answer("<title>ok</title>"))
    backend = PinnedPublicBackend(lambda host, port: [PUBLIC, OTHER_PUBLIC], network)
    backend.connect_tcp("shop.example", 80)
    assert network.connected == [(PUBLIC, 80), (OTHER_PUBLIC, 80)]


def test_a_redirect_to_an_internal_host_is_checked_again_and_refused(hook_dns):
    network = FakeNetwork(
        _answer("", status="302 Found", headers=("Location: http://inside.example/admin",)),
        _answer("<title>secret</title>"),
    )
    resolver = lambda host, port: [PUBLIC] if host == "shop.example" else ["192.168.1.10"]  # noqa: E731

    assert _fetcher(network, resolver).title("http://shop.example/p") == ""
    assert network.connected == [(PUBLIC, 80)]  # the redirect hop never got a connection


def test_a_redirect_to_a_public_host_is_followed_on_its_own_checked_address(hook_dns):
    network = FakeNetwork(
        _answer("", status="301 Moved", headers=("Location: http://www.shop.example/p",)),
        _answer("<title>Клемма</title>"),
    )
    resolver = lambda host, port: [PUBLIC] if host == "shop.example" else [OTHER_PUBLIC]  # noqa: E731

    page = fetch_page(supplier_client(SupplierSiteTransport(resolver=resolver, backend=network, exceptions=())), "http://shop.example/p")
    assert page is not None and page.host == "www.shop.example" and page.tls_checked
    assert network.connected == [(PUBLIC, 80), (OTHER_PUBLIC, 80)]


# ── TLS ──


def test_certificates_are_checked_by_default_with_the_host_name_as_sni(hook_dns):
    import ssl

    network = FakeNetwork(_answer("<title>Разъём</title>"))
    page = fetch_page(
        supplier_client(SupplierSiteTransport(resolver=lambda h, p: [PUBLIC], backend=network, exceptions=())),
        "https://shop.example/p",
    )
    assert page is not None and page.tls_checked is True
    assert network.connected == [(PUBLIC, 443)]
    assert network.tls == [("shop.example", ssl.CERT_REQUIRED)]


def test_only_a_listed_domain_is_read_without_the_check_and_the_page_says_so(hook_dns):
    import ssl

    network = FakeNetwork(_answer("<title>Старый сайт</title>"), _answer("<title>Другой</title>"))
    client = supplier_client(SupplierSiteTransport(
        resolver=lambda h, p: [PUBLIC], backend=network, exceptions=("old-shop.example",),
    ))

    listed = fetch_page(client, "https://www.old-shop.example/p")
    other = fetch_page(client, "https://not-old-shop.example/p")

    assert listed is not None and listed.tls_checked is False
    assert other is not None and other.tls_checked is True
    assert network.tls == [("www.old-shop.example", ssl.CERT_NONE), ("not-old-shop.example", ssl.CERT_REQUIRED)]


def test_the_exception_list_comes_from_the_environment_only(monkeypatch):
    monkeypatch.delenv("ERP_TLS_UNVERIFIED_HOSTS", raising=False)
    assert tls_exception_hosts() == ()
    monkeypatch.setenv("ERP_TLS_UNVERIFIED_HOSTS", " Old-Shop.Example. , ,b.example")
    assert tls_exception_hosts() == ("b.example", "old-shop.example")
    assert SupplierSiteTransport().exceptions == ("b.example", "old-shop.example")
    assert tls_unverified("shop.old-shop.example", ("old-shop.example",))
    assert not tls_unverified("evilold-shop.example", ("old-shop.example",))


def test_the_default_client_verifies_and_ignores_proxy_variables(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
    monkeypatch.delenv("ERP_TLS_UNVERIFIED_HOSTS", raising=False)
    client = HttpPageFetcher()._client
    transport = client._transport
    assert isinstance(transport, SupplierSiteTransport)
    assert client._mounts == {}  # no proxy taken from the environment
    # the httpx internals the transport relies on are still there (a dependency upgrade must not drop the pin)
    assert isinstance(transport._verified._pool._network_backend, PinnedPublicBackend)
    assert transport._verified._pool._ssl_context.verify_mode.name == "CERT_REQUIRED"
    assert transport._unverified._pool._ssl_context.verify_mode.name == "CERT_NONE"


def test_the_hook_refuses_an_internal_literal_before_any_lookup_by_the_transport(hook_dns):
    hook_dns("127.0.0.1")
    network = FakeNetwork(_answer("<title>secret</title>"))
    resolver = SequenceResolver([PUBLIC])
    assert _fetcher(network, resolver).title("http://127.0.0.1/") == ""
    assert network.connected == [] and resolver.asked == []
