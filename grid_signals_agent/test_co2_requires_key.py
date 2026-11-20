"""Regression test for issue #9: with no usable Electricity Maps key the agent
must build no API client, schedule no CO2 job and make no external request.
"""
import copy
import logging
import types

import pytest
import requests

from derhost.compat.import_hook import install_volttron_compatibility

install_volttron_compatibility()

import grid_signals_agent.agent as agent_module  # noqa: E402 (must follow the compat install)

MARKER = "TESTKEY-DO-NOT-USE-4b81d6e0"
OFF_LINE = "CO2 signals off: no Electricity Maps key"


def _co2_contents(key):
    api_information = {"zone": "US-TEST"}
    if key is not None:
        api_information["API_key"] = key
    return {
        "type_of_grid_service_signals": {
            "co2": {"real-time": True, "method": "API", "API_information": api_information}
        }
    }


def _make_agent(tmp_path):
    agent = agent_module.GridSignalAgent(config_path=str(tmp_path / "missing.json"))
    scheduled = []
    agent.core = types.SimpleNamespace(
        identity="test-agent",
        schedule=lambda when, func: scheduled.append(func),
    )
    return agent, scheduled


@pytest.fixture
def recorded_requests(monkeypatch):
    calls = []

    def fake_request(self, method, url, **kwargs):
        calls.append((method, url))
        raise requests.ConnectionError("blocked by test")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return calls


def _run_scheduled(scheduled):
    for func in scheduled:
        func()


def _off_records(caplog):
    return [r for r in caplog.records if r.getMessage() == OFF_LINE]


def _assert_co2_off(agent, scheduled, caplog, recorded_requests):
    assert len(_off_records(caplog)) == 1
    _run_scheduled(scheduled)
    assert recorded_requests == []
    assert getattr(agent, "co2_signal", None) is None
    assert scheduled == []


@pytest.mark.parametrize("key", [None, "", "   ", "<your_electricity_maps_api_key>", " <placeholder>"])
def test_unusable_key_in_config_turns_co2_off(tmp_path, caplog, recorded_requests, key):
    agent, scheduled = _make_agent(tmp_path)
    contents = _co2_contents(key)
    contents["type_of_grid_service_signals"].pop("price", None)
    with caplog.at_level(logging.INFO):
        agent.configure_main("config", "NEW", contents)
    _assert_co2_off(agent, scheduled, caplog, recorded_requests)


def test_built_in_default_config_turns_co2_off(tmp_path, caplog, recorded_requests):
    agent, scheduled = _make_agent(tmp_path)
    default = copy.deepcopy(agent.default_config)
    assert default["type_of_grid_service_signals"]["co2"]["API_information"]["API_key"].startswith("<")
    default["type_of_grid_service_signals"].pop("price")
    with caplog.at_level(logging.INFO):
        agent.configure_main("config", "NEW", {"type_of_grid_service_signals": default["type_of_grid_service_signals"]})
    _assert_co2_off(agent, scheduled, caplog, recorded_requests)


def test_usable_key_schedules_the_co2_jobs(tmp_path, caplog, recorded_requests):
    agent, scheduled = _make_agent(tmp_path)
    contents = _co2_contents(MARKER)
    with caplog.at_level(logging.INFO):
        agent.configure_main("config", "NEW", contents)

    assert _off_records(caplog) == []
    assert agent.co2_signal.api_key == MARKER
    assert agent.co2_signal.zone == "US-TEST"
    co2_jobs = [f for f in scheduled if f.__name__ in ("generate_next_24_co2_signal", "generate_real_time_co2")]
    assert sorted(f.__name__ for f in co2_jobs) == [
        "generate_next_24_co2_signal",
        "generate_next_24_co2_signal",
        "generate_real_time_co2",
    ]
    # Running a job reaches the recording request, so the zero cases above can fail.
    co2_jobs[0]()
    assert len(recorded_requests) > 0
