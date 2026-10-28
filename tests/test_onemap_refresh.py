from pathlib import Path
import pytest

from app.stage2a.provider import OneMapClient
from app.stage2a.transport import ProviderError


class FakeTokens:
    def __init__(self):
        self.refreshes = 0
        self.invalidations = 0

    def get(self, transport=None, *, force_refresh=False):
        if force_refresh:
            self.refreshes += 1
            return "fresh-token"
        return "old-token"

    def can_refresh(self):
        return True

    def invalidate_runtime(self):
        self.invalidations += 1


class PayloadAuthTransport:
    def __init__(self, second_payload):
        self.calls = []
        self.second_payload = second_payload

    def request(self, operation, *, token=None, params=None, body=None):
        self.calls.append((operation, token))
        if len(self.calls) == 1:
            return {"error": "token expired"}
        return self.second_payload


def test_payload_auth_rejection_refreshes_once_then_search_succeeds():
    transport = PayloadAuthTransport({
        "found": 0, "totalNumPages": 0, "pageNum": 1, "results": []
    })
    client = OneMapClient(Path("."), transport=transport)
    client.tokens = FakeTokens()

    result = client.search("museum", 1)

    assert result["results"] == []
    assert transport.calls == [("search", "old-token"), ("search", "fresh-token")]
    assert client.tokens.refreshes == 1
    assert client.tokens.invalidations == 1


def test_payload_auth_rejection_after_refresh_does_not_loop():
    transport = PayloadAuthTransport({"error": "token still invalid"})
    client = OneMapClient(Path("."), transport=transport)
    client.tokens = FakeTokens()

    with pytest.raises(ProviderError) as exc:
        client.search("museum", 1)

    assert exc.value.code == "AUTH_REJECTED"
    assert len(transport.calls) == 2
    assert client.tokens.refreshes == 1
