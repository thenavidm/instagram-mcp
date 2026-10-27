from __future__ import annotations

import json

import pytest

from instagram_mcp.config import (
    FACEBOOK_HOST,
    Account,
    ConfigError,
    Settings,
    Tier,
    load_settings,
    pick,
)


def test_exact_match_beats_prefix_match():
    """The regression this ordering exists to prevent.

    "Acme Media" starts with "acme m". A pure prefix match hands an unnamed
    post to the wrong account whenever both are connected.
    """
    settings = Settings(
        accounts=[
            Account(name="Acme Media", user_id="1", token="t"),
            Account(name="acme m", user_id="2", token="t"),
        ],
        preferred=["acme m"],
    )
    assert pick(settings).user_id == "2"


def test_preference_order_is_followed():
    settings = Settings(
        accounts=[
            Account(name="second", user_id="2", token="t"),
            Account(name="first", user_id="1", token="t"),
        ],
        preferred=["first", "second"],
    )
    assert pick(settings).name == "first"


def test_falls_back_to_first_when_no_preference_matches():
    settings = Settings(accounts=[Account(name="only", user_id="1", token="t")], preferred=["nope"])
    assert pick(settings).name == "only"


def test_named_account_is_matched_ignoring_at_sign_and_case():
    settings = Settings(accounts=[Account(name="AcmeM", user_id="9", token="t")])
    assert pick(settings, "@acmem").user_id == "9"


def test_unknown_account_lists_what_is_connected():
    settings = Settings(accounts=[Account(name="a", user_id="1", token="t")])
    with pytest.raises(ConfigError, match="Connected: a"):
        pick(settings, "b")


def test_host_filter_explains_which_tier_is_missing():
    settings = Settings(accounts=[Account(name="a", user_id="1", token="t")])
    with pytest.raises(ConfigError, match="Facebook Login"):
        pick(settings, host=FACEBOOK_HOST)


def test_tier_is_derived_from_host():
    assert Account(name="a", user_id="1", token="t").tier is Tier.INSTAGRAM_LOGIN
    assert Account(name="a", user_id="1", token="t", host=FACEBOOK_HOST).tier is Tier.FACEBOOK_LOGIN


def test_accounts_file_rejects_a_bad_host(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps([{"user_id": "1", "access_token": "t", "host": "example.com"}]))
    with pytest.raises(ConfigError, match="not a Graph host"):
        load_settings(env={"IG_ACCOUNTS_FILE": str(path), "IG_MCP_DATA_DIR": str(tmp_path)})


def test_accounts_file_names_the_missing_field(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps([{"user_id": "1"}]))
    with pytest.raises(ConfigError, match="access_token"):
        load_settings(env={"IG_ACCOUNTS_FILE": str(path), "IG_MCP_DATA_DIR": str(tmp_path)})


def test_no_configuration_gives_an_actionable_error():
    settings = Settings()
    with pytest.raises(ConfigError, match="doctor"):
        pick(settings)
