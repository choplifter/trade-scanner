"""Every Alpaca REST client carries a socket timeout -- app.alpaca.client.
with_timeout. alpaca-py sets none, and on 2026-09-21 three option-chain
fetches sat in recv() for two hours on connections a network drop had
killed, freezing the scanner engine's loop with them."""

import socket
import threading
import time

import pytest
import requests
from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from alpaca.trading.client import TradingClient

from app.alpaca.client import REST_TIMEOUT, _TimeoutAdapter, with_timeout


@pytest.mark.parametrize(
    "client_cls",
    [TradingClient, StockHistoricalDataClient, NewsClient, ScreenerClient, OptionHistoricalDataClient],
)
def test_the_timeout_is_mounted_on_every_client_kind(client_cls):
    # Also the tripwire for an alpaca-py upgrade: with_timeout reaches into
    # the private _session, and this fails if it moves.
    client = with_timeout(client_cls(api_key="key", secret_key="secret"))
    adapter = client._session.get_adapter("https://data.alpaca.markets/v1beta1/options")
    assert isinstance(adapter, _TimeoutAdapter)
    assert adapter._timeout == REST_TIMEOUT


class _StallingServer:
    """Answers with the headers of a chunked response and then says nothing
    more -- what a connection looks like from the client side once the
    network under it has gone."""

    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen()
        self.port = self._sock.getsockname()[1]
        self._conns: list[socket.socket] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self._conns.append(conn)
            conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )

    def close(self) -> None:
        for conn in self._conns:
            conn.close()
        self._sock.close()


def test_a_stalled_option_chain_gives_up_instead_of_hanging():
    server = _StallingServer()
    try:
        client = with_timeout(
            OptionHistoricalDataClient(
                api_key="key", secret_key="secret", url_override=f"http://127.0.0.1:{server.port}"
            ),
            timeout=(1.0, 0.5),
        )
        started = time.monotonic()
        with pytest.raises(requests.exceptions.ConnectionError):
            client.get_option_chain(OptionChainRequest(underlying_symbol="SPY"))
        assert time.monotonic() - started < 5
    finally:
        server.close()


@pytest.mark.parametrize("caller_timeout, expected", [(None, (10.0, 30.0)), (3, 3)])
def test_the_default_applies_only_when_the_caller_set_none(monkeypatch, caller_timeout, expected):
    seen = {}

    def recording_send(self, request, timeout=None, **kwargs):
        seen["timeout"] = timeout
        raise requests.exceptions.ConnectionError("stop here")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", recording_send)
    session = requests.Session()
    session.mount("http://", _TimeoutAdapter((10.0, 30.0)))
    with pytest.raises(requests.exceptions.ConnectionError):
        session.get("http://example.invalid/", timeout=caller_timeout)
    assert seen["timeout"] == expected
