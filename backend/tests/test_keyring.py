"""Several API keys for one provider, and what happens when one fails.

The rotation only ever runs on the unhappy path, which is exactly the path
nobody exercises by hand - so it is tested here rather than discovered
halfway through an import that has already spent the day's quota.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import keyring                                    # noqa: E402
from app.llm.providers import _post_with_retries               # noqa: E402


@pytest.fixture(autouse=True)
def clean_ring():
    keyring.forget_all()
    yield
    keyring.forget_all()


class _Resp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return {}


class _Client:
    """Records which key each attempt used, and answers from a script."""

    def __init__(self, script):
        self.script = list(script)
        self.keys_seen = []

    def post(self, url, json=None, headers=None):
        self.keys_seen.append(headers.get("x-goog-api-key"))
        return self.script.pop(0) if self.script else _Resp(200)


def _ring(*labels):
    return [keyring.ApiKey(label, f"secret-{label}") for label in labels]


def _headers_for(secret):
    return {"x-goog-api-key": secret}


def test_a_rate_limited_key_hands_off_to_the_next_one():
    """A 429 must cost another KEY, not another minute.

    A free tier meters per minute and per day, and a 429 does not say which
    ceiling was hit. Sleeping clears the first and never clears the second,
    so an import that trips the daily cap stalls for the full backoff and
    then fails anyway. Asking a different key is correct for both.
    """
    client = _Client([_Resp(429), _Resp(200)])
    resp = _post_with_retries(client, "http://x", json={}, provider="Gemini",
                              keys=_ring("a", "b"), headers_for=_headers_for)

    assert resp.status_code == 200
    assert client.keys_seen == ["secret-a", "secret-b"]


def test_a_rejected_key_is_not_offered_again():
    """401 is not a wait-and-see. A revoked key will not start working, and
    retrying it on every later call turns one bad paste into a stall."""
    client = _Client([_Resp(401), _Resp(200)])
    ring = _ring("bad", "good")

    resp = _post_with_retries(client, "http://x", json={}, provider="Gemini",
                              keys=ring, headers_for=_headers_for)
    assert resp.status_code == 200

    states = {k["label"]: k["state"] for k in keyring.status(ring)}
    assert states["bad"] == "rejected"
    assert states["good"] == "ready"

    # A later call skips it without spending an attempt on it.
    again = _Client([_Resp(200)])
    _post_with_retries(again, "http://x", json={}, provider="Gemini",
                       keys=ring, headers_for=_headers_for)
    assert again.keys_seen == ["secret-good"]


def test_a_key_that_works_stops_resting():
    """A cooldown is a guess about the next minute, not a fact about the
    account. The key answering is the evidence that retires the guess."""
    ring = _ring("a")
    keyring.rest(ring[0], seconds=300)
    assert keyring.status(ring)[0]["state"] == "resting"

    _post_with_retries(_Client([_Resp(200)]), "http://x", json={},
                       provider="Gemini", keys=ring, headers_for=_headers_for)
    assert keyring.status(ring)[0]["state"] == "ready"


def test_every_key_rate_limited_falls_back_to_waiting(monkeypatch):
    """Rotation first, backoff second - but the backoff must still exist.

    With every key resting there is nothing else to ask, and giving up
    immediately would turn a one-minute burst limit into a failed import.
    """
    slept = []
    monkeypatch.setattr("app.llm.providers.time.sleep", slept.append)

    client = _Client([_Resp(429), _Resp(429), _Resp(200)])
    resp = _post_with_retries(client, "http://x", json={}, provider="Gemini",
                              keys=_ring("a", "b"), headers_for=_headers_for)

    assert resp.status_code == 200
    assert client.keys_seen[:2] == ["secret-a", "secret-b"]
    assert slept, "should have waited once both keys were resting"


def test_one_key_behaves_exactly_as_before(monkeypatch):
    """The single-key path must not have grown a new failure mode."""
    slept = []
    monkeypatch.setattr("app.llm.providers.time.sleep", slept.append)

    client = _Client([_Resp(429), _Resp(200)])
    resp = _post_with_retries(client, "http://x", json={}, provider="Gemini",
                              keys=_ring("only"), headers_for=_headers_for)

    assert resp.status_code == 200
    assert client.keys_seen == ["secret-only", "secret-only"]
    assert slept, "a lone key has nothing to rotate to, so it waits"


def test_no_keys_at_all_still_posts():
    """Callers that pass plain headers keep working untouched."""
    class _Plain:
        def __init__(self):
            self.headers_seen = []

        def post(self, url, json=None, headers=None):
            self.headers_seen.append(headers)
            return _Resp(200)

    client = _Plain()
    resp = _post_with_retries(client, "http://x", json={},
                              headers={"Authorization": "Bearer k"},
                              provider="OpenRouter")
    assert resp.status_code == 200
    assert client.headers_seen == [{"Authorization": "Bearer k"}]


def test_the_same_key_twice_is_one_key():
    """Rotating onto a duplicate spends the request the rotation saved."""
    duplicated = [keyring.ApiKey("a", "same"), keyring.ApiKey("b", "same"),
                  keyring.ApiKey("c", "other")]
    assert [k.label for k in keyring.dedupe(duplicated)] == ["a", "c"]


def test_a_stored_list_and_a_bare_string_both_parse():
    """The single-key field predates the list and must keep working."""
    assert keyring.parse('[{"label":"work","key":"k1"}]')[0].label == "work"
    assert keyring.parse("plain-secret")[0].secret == "plain-secret"
    assert keyring.parse("") == []
    # A label-less entry still gets something a person can point at.
    assert keyring.parse('["k1","k2"]')[1].label == "Key 2"


def test_a_secret_is_never_returned_whole():
    """`status` feeds the Settings screen, which must not leak the key."""
    ring = _ring("work")
    shown = keyring.status(ring)[0]
    assert "secret-work" not in shown["hint"]
    assert shown["hint"].startswith("secr") and shown["hint"].endswith("work")


# ------------------------------------------------------- storing the ring

def test_the_ring_actually_persists(tmp_db):
    """`save_settings` drops keys it does not know, and did not know this one.

    The write reported success, the response was built from the request
    that had just been handed in, and the setting was simply gone on the
    next page load - which presents as "I click save and everything
    resets". A setting added to the override list and not to
    SETTING_DEFAULTS is silently discarded.
    """
    from app.db import repository as repo

    assert "llm_api_keys" in repo.SETTING_DEFAULTS
    repo.save_settings(tmp_db, {"llm_api_keys": '[{"label":"a","key":"k1"}]'})
    assert repo.get_settings(tmp_db)["llm_api_keys"] == '[{"label":"a","key":"k1"}]'


def test_every_llm_override_can_be_stored(tmp_db):
    """The two lists have to agree, or a setting is write-only by accident."""
    from app.db import repository as repo
    from app.llm import settings as llm_settings

    missing = [k for k in llm_settings.OVERRIDE_KEYS
               if k not in repo.SETTING_DEFAULTS]
    assert not missing, f"cannot be persisted: {missing}"


# --------------------------------------------- how a provider says "bad key"

def test_a_four_hundred_that_means_bad_key_rotates():
    """Google does not use 401 for a bad credential.

    A mistyped or revoked key comes back `400 INVALID_ARGUMENT` with "API
    key not valid" in the body - the same status a malformed request gets.
    Keying the check on the status alone meant the commonest failure there
    is, somebody pasting a key with a character missing, never retired the
    key, never rotated past it, and failed every call for the life of the
    process.
    """
    body = ('{"error":{"code":400,"status":"INVALID_ARGUMENT",'
            '"message":"API key not valid. Please pass a valid API key."}}')

    class _Resp:
        def __init__(self, status_code, text=""):
            self.status_code = status_code
            self.text = text
            self.headers = {}

        def json(self):
            return {}

    class _Client:
        def __init__(self, script):
            self.script = list(script)
            self.keys_seen = []

        def post(self, url, json=None, headers=None):
            self.keys_seen.append(headers.get("x-goog-api-key"))
            return self.script.pop(0)

    ring = [keyring.ApiKey("bad", "secret-bad"),
            keyring.ApiKey("good", "secret-good")]
    client = _Client([_Resp(400, body), _Resp(200)])

    resp = _post_with_retries(client, "http://x", json={}, provider="Gemini",
                              keys=ring, headers_for=_headers_for)

    assert resp.status_code == 200
    assert client.keys_seen == ["secret-bad", "secret-good"]
    assert keyring.status(ring)[0]["state"] == "rejected"


def test_a_four_hundred_about_the_payload_does_not_blame_the_key():
    """400 also means "your request was wrong", and standing a working key
    down for that would turn one bad schema into a dead workspace."""
    from app.llm.providers import _rejects_the_key

    class _Resp:
        status_code = 400
        text = ('{"error":{"message":"Invalid JSON payload received. '
                'Unknown name \\"type\\" at generation_config."}}')

    assert not _rejects_the_key(_Resp())


# ---------------------------------------------- losing keys, and undoing it

def test_replacing_the_key_list_keeps_what_it_replaced(tmp_db):
    """A key list is a full REPLACE, and a credential cannot be regenerated.

    Every other thing in this app rebuilds from the user's own documents -
    statements re-import, categories re-derive, the whole ledger recomputes.
    A revoked API key does not. So one partial save must not be the end of
    it: a cleanup sending `[{"label": "Default key"}]`, meaning to drop one
    test key, removed three real ones and left no record that it had.
    """
    from app.db import repository as repo

    four = ('[{"label":"Default key","key":"k1"},{"label":"Teerex-key","key":"k2"},'
            '{"label":"swan","key":"k3"},{"label":"echo","key":"k4"}]')
    repo.save_settings(tmp_db, {"llm_api_keys": four})
    assert not repo.get_settings(tmp_db).get("llm_api_keys_previous")

    # The mistake.
    repo.save_settings(tmp_db, {"llm_api_keys": '[{"label":"Default key","key":"k1"}]'})

    stored = repo.get_settings(tmp_db)
    assert len(keyring.parse(stored["llm_api_keys"])) == 1
    assert stored["llm_api_keys_previous"] == four, "the four must be recoverable"


def test_an_unchanged_save_does_not_burn_the_undo(tmp_db):
    """Saving the same list twice must not overwrite the stash with itself,
    or the one save that mattered is no longer the one being held."""
    from app.db import repository as repo

    two = '[{"label":"a","key":"k1"},{"label":"b","key":"k2"}]'
    repo.save_settings(tmp_db, {"llm_api_keys": two})
    repo.save_settings(tmp_db, {"llm_api_keys": '[{"label":"a","key":"k1"}]'})
    assert repo.get_settings(tmp_db)["llm_api_keys_previous"] == two

    # Re-saving the now-current list changes nothing, so the stash stands.
    repo.save_settings(tmp_db, {"llm_api_keys": '[{"label":"a","key":"k1"}]'})
    assert repo.get_settings(tmp_db)["llm_api_keys_previous"] == two


def test_the_stash_is_never_served_over_http(signed_in_client):
    """It holds the same secrets as the list it shadows."""
    body = signed_in_client.get("/api/settings").json()
    assert "llm_api_keys" not in body
    assert "llm_api_keys_previous" not in body

    llm = signed_in_client.get("/api/settings/llm").json()
    assert "llm_api_keys_previous" not in llm
    # Only masked hints reach the browser.
    for k in llm.get("api_keys") or []:
        assert "key" not in k


# ---------------------------------------------------------------------------
# A timeout is a reason to try another key, not only to wait
# ---------------------------------------------------------------------------

class _FlakyClient:
    """Raises a transport error for the first `fail` attempts, then answers."""

    def __init__(self, fail: int, exc=None):
        import httpx
        self.remaining = fail
        self.exc = exc or httpx.ReadTimeout("too slow")
        self.keys_seen: list[str] = []

    def post(self, url, json=None, headers=None):
        self.keys_seen.append(headers.get("x-goog-api-key"))
        if self.remaining > 0:
            self.remaining -= 1
            raise self.exc
        return _Resp(200)


def test_a_timed_out_key_hands_off_to_the_next_one(monkeypatch):
    """A timeout used to sleep on the same key instead of asking another.

    The key was never marked as tried, so the next attempt picked the first
    available key again - the same one - and backed off: two seconds, then
    four, then eight. Fourteen seconds of waiting with three good keys
    sitting idle, which is what a whole eval sweep was doing.
    """
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    client = _FlakyClient(fail=1)
    resp = _post_with_retries(client, "http://x", json={}, provider="Gemini",
                              keys=_ring("a", "b"), headers_for=_headers_for)

    assert resp.status_code == 200
    assert client.keys_seen == ["secret-a", "secret-b"]
    assert slept == [], "it waited when another key was available"


def test_a_timeout_does_not_stand_a_good_key_down(monkeypatch):
    """The key is set aside for THIS call only.

    A timeout is the provider being slow, not the credential being bad.
    Resting it - as a 429 does - would shrink the ring for every later
    call over something that was never the key's fault.
    """
    monkeypatch.setattr("time.sleep", lambda s: None)

    client = _FlakyClient(fail=1)
    _post_with_retries(client, "http://x", json={}, provider="Gemini",
                       keys=_ring("a", "b"), headers_for=_headers_for)

    ring = _ring("a", "b")
    assert len(keyring.available(ring)) == 2, (
        "a slow reply took a working key out of service")


def test_only_when_every_key_has_timed_out_does_it_wait(monkeypatch):
    """Backoff is still there - it is just the last resort rather than the
    first, which is the same order the 429 path already used."""
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    client = _FlakyClient(fail=2)
    resp = _post_with_retries(client, "http://x", json={}, provider="Gemini",
                              keys=_ring("a", "b"), headers_for=_headers_for)

    assert resp.status_code == 200
    assert client.keys_seen[:2] == ["secret-a", "secret-b"]
    assert slept, "both keys failed and it did not back off at all"


def test_a_single_key_still_backs_off(monkeypatch):
    """Nothing to rotate to, so the old behaviour is the right behaviour."""
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    client = _FlakyClient(fail=1)
    resp = _post_with_retries(client, "http://x", json={}, provider="Gemini",
                              keys=_ring("solo"), headers_for=_headers_for)

    assert resp.status_code == 200
    assert slept == [2.0]
