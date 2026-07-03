import pytest
from unittest.mock import MagicMock, patch
from TestData.three_month_seed.sources.manual import ManualClient


def test_login_called_once_per_persona(monkeypatch):
    client = ManualClient(base_url="http://localhost")
    fake_post = MagicMock(return_value=MagicMock(
        status_code=200, json=lambda: {"token": "T1"}, raise_for_status=lambda: None,
    ))
    monkeypatch.setattr(client._http, "post", fake_post)
    t1 = client.token_for("rodrigo@borgia.family", "password")
    t2 = client.token_for("rodrigo@borgia.family", "password")
    assert t1 == "T1" == t2
    fake_post.assert_called_once()


def test_separate_personas_each_login(monkeypatch):
    client = ManualClient(base_url="http://localhost")
    calls = {"n": 0}
    def fake_post(*a, **kw):
        calls["n"] += 1
        return MagicMock(
            status_code=200, json=lambda: {"token": f"T{calls['n']}"},
            raise_for_status=lambda: None,
        )
    monkeypatch.setattr(client._http, "post", fake_post)
    client.token_for("rodrigo@borgia.family", "password")
    client.token_for("vannozza@borgia.family", "password")
    assert calls["n"] == 2


def test_login_failure_raises():
    client = ManualClient(base_url="http://localhost")
    with patch.object(client._http, "post") as p:
        p.return_value = MagicMock(status_code=401,
            json=lambda: {"error": "bad creds"},
            raise_for_status=MagicMock(side_effect=Exception("401")))
        with pytest.raises(Exception):
            client.token_for("ghost@nowhere.invalid", "password")
