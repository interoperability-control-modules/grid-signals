"""Tests for issue #10: an unknown or missing price type is refused at
configure time and publishes nothing, instead of raising on every run.
"""
import json
import logging
from datetime import datetime

import pytest

from derhost.compat.import_hook import install_volttron_compatibility

install_volttron_compatibility()

import grid_signals_agent.agent as agent_module  # noqa: E402 (must follow the compat install)

TIERS = {"off-peak": 0.11, "mid-peak": 0.22, "on-peak": 0.33}
INTERVALS = {
    "off-peak": [[0, 7], [21, 24]],
    "mid-peak": [[7, 10], [18, 21]],
    "on-peak": [[10, 18]],
}
FROZEN_HOUR = 5


def _tier_for_hour(hour):
    for name, spans in INTERVALS.items():
        if any(start <= hour < end for start, end in spans):
            return TIERS[name]
    raise AssertionError(f"hour {hour} has no tier")


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 15, FROZEN_HOUR, 30)


class _Core:
    identity = "der.grid_signals"

    def __init__(self):
        self.scheduled = []

    def schedule(self, when, func):
        self.scheduled.append(func)


def _price_block(**overrides):
    block = {
        "type_of_price_signal": "TOU",
        "TOU_pricing": {"interval": INTERVALS, "pricing": TIERS},
    }
    block.update(overrides)
    return block


def _make_agent(tmp_path, monkeypatch, price_block):
    config = {
        "campus": "PNNL",
        "run_dayahead_schedule": "0 0 * * *",
        "run_realtime_schedule": "0 * * * *",
        "type_of_grid_service_signals": {"price": price_block},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    agent = agent_module.GridSignalAgent(config_path=str(path))
    core = _Core()
    published = []
    monkeypatch.setattr(agent, "core", core)
    monkeypatch.setattr(agent, "publish_data",
                        lambda headers, message, topic: published.append((headers, message, topic)))
    monkeypatch.setattr(agent_module, "datetime", _FrozenDatetime)
    return agent, core, published


def _configure(agent):
    agent.configure_main("config", "NEW", {})


def test_tou_config_publishes_24_hourly_tiers(tmp_path, monkeypatch):
    agent, core, published = _make_agent(tmp_path, monkeypatch, _price_block())

    _configure(agent)
    assert len(core.scheduled) == 2
    assert all(f == agent.generate_price_signal for f in core.scheduled)

    agent.generate_price_signal()

    assert len(published) == 24
    for i, (headers, message, topic) in enumerate(published):
        assert topic == "devices/PNNL/grid_information/price/all"
        assert isinstance(message[0]["tou"], float)
        assert message[0]["tou"] == _tier_for_hour((FROZEN_HOUR + i) % 24)
        assert message[1]["tou"]["type"] == "float"
        assert "Date" in headers


@pytest.mark.parametrize("bad_type", ["RTP", "", 7, None])
def test_unsupported_type_is_refused_and_publishes_nothing(tmp_path, monkeypatch, caplog, bad_type):
    agent, core, published = _make_agent(
        tmp_path, monkeypatch, _price_block(type_of_price_signal=bad_type))

    with caplog.at_level(logging.ERROR):
        _configure(agent)
        agent.generate_price_signal()

    assert core.scheduled == []
    assert published == []
    assert repr(bad_type) in caplog.text


def test_missing_type_is_refused_and_publishes_nothing(tmp_path, monkeypatch, caplog):
    block = _price_block()
    del block["type_of_price_signal"]
    agent, core, published = _make_agent(tmp_path, monkeypatch, block)

    with caplog.at_level(logging.ERROR):
        _configure(agent)
        agent.generate_price_signal()

    assert core.scheduled == []
    assert published == []
    assert "None" in caplog.text
    assert "type_of_price_signal" in caplog.text


def test_lowercase_tou_is_still_accepted(tmp_path, monkeypatch):
    agent, core, published = _make_agent(
        tmp_path, monkeypatch, _price_block(type_of_price_signal="tou"))

    _configure(agent)
    agent.generate_price_signal()

    assert len(core.scheduled) == 2
    assert len(published) == 24


def _error_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


@pytest.mark.parametrize("bad_type", ["RTP", "", 7, None])
def test_configure_alone_logs_refusal_naming_the_type(tmp_path, monkeypatch, caplog, bad_type):
    agent, core, published = _make_agent(
        tmp_path, monkeypatch, _price_block(type_of_price_signal=bad_type))

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        _configure(agent)

    errors = _error_messages(caplog)
    assert len(errors) == 1
    assert errors[0].startswith("Price signal not scheduled")
    assert repr(bad_type) in errors[0]
    assert core.scheduled == []


def test_configure_alone_logs_refusal_for_missing_type(tmp_path, monkeypatch, caplog):
    block = _price_block()
    del block["type_of_price_signal"]
    agent, core, published = _make_agent(tmp_path, monkeypatch, block)

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        _configure(agent)

    errors = _error_messages(caplog)
    assert len(errors) == 1
    assert errors[0].startswith("Price signal not scheduled")
    assert "type_of_price_signal None" in errors[0]
    assert core.scheduled == []


def test_tou_without_tiers_is_refused_and_publishes_nothing(tmp_path, monkeypatch, caplog):
    block = _price_block()
    del block["TOU_pricing"]
    agent, core, published = _make_agent(tmp_path, monkeypatch, block)

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        _configure(agent)
        agent.generate_price_signal()

    errors = _error_messages(caplog)
    assert len(errors) == 2
    assert all("TOU_pricing" in e for e in errors)
    assert core.scheduled == []
    assert published == []
