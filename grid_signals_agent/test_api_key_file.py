"""Tests for issue #12: the Electricity Maps key can come from the file named by
ELECTRICITY_MAPS_API_KEY_FILE, and it never reaches stdout, stderr or a log record.
"""
import logging
import types

import pytest
import requests

from derhost.compat.import_hook import install_volttron_compatibility

install_volttron_compatibility()

import grid_signals_agent.agent as agent_module  # noqa: E402 (must follow the compat install)

ENV = "ELECTRICITY_MAPS_API_KEY_FILE"
FILE_MARKER = "FILEKEY-DO-NOT-USE-5c1e8a37"
CONFIG_MARKER = "CFGKEY-DO-NOT-USE-77d2b9f4"
OFF_LINE = "CO2 signals off: no Electricity Maps key"
IGNORED_LINE = "API_key in config ignored: %s is set" % ENV


def _contents(config_key=CONFIG_MARKER):
    api_information = {"zone": "US-TEST"}
    if config_key is not None:
        api_information["API_key"] = config_key
    return {
        "type_of_grid_service_signals": {
            "price": {"type_of_price_signal": "TOU"},
            "co2": {"real-time": True, "method": "API", "API_information": api_information},
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


def _key_file(tmp_path, text):
    path = tmp_path / "key"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def recorded_requests(monkeypatch):
    calls = []

    def fake_request(self, method, url, **kwargs):
        calls.append((method, url))
        raise requests.ConnectionError("blocked by test")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return calls


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


def test_file_key_is_used_and_config_key_is_ignored_by_field_name(tmp_path, monkeypatch, caplog):
    path = _key_file(tmp_path, "\n  " + FILE_MARKER + "  \n")
    # The marker must be in the file, or the absence checks below prove nothing.
    assert FILE_MARKER in path.read_text(encoding="utf-8")
    monkeypatch.setenv(ENV, str(path))
    agent, scheduled = _make_agent(tmp_path)
    with caplog.at_level(logging.DEBUG):
        agent.configure_main("config", "NEW", _contents())

    assert agent.co2_signal.api_key == FILE_MARKER
    assert agent.co2_signal.zone == "US-TEST"
    assert len(scheduled) > 0
    assert IGNORED_LINE in _messages(caplog)
    assert [m for m in _messages(caplog) if m == OFF_LINE] == []


def test_key_reaches_no_stdout_stderr_or_log_record(tmp_path, monkeypatch, caplog, capsys):
    path = _key_file(tmp_path, FILE_MARKER)
    monkeypatch.setenv(ENV, str(path))
    agent, _ = _make_agent(tmp_path)
    with caplog.at_level(logging.DEBUG):
        agent.configure_main("config", "NEW", _contents())
        agent.configure_main("config", "UPDATE", _contents())

    # The file key must have been read, or the absence checks below prove nothing.
    assert agent.co2_signal.api_key == FILE_MARKER
    captured = capsys.readouterr()
    for marker in (FILE_MARKER, CONFIG_MARKER):
        assert marker not in captured.out
        assert marker not in captured.err
        for message in _messages(caplog):
            assert marker not in message
    assert len(caplog.records) > 0


def test_update_rereads_the_file(tmp_path, monkeypatch):
    path = _key_file(tmp_path, FILE_MARKER)
    monkeypatch.setenv(ENV, str(path))
    agent, _ = _make_agent(tmp_path)
    agent.configure_main("config", "NEW", _contents(None))
    assert agent.co2_signal.api_key == FILE_MARKER
    path.write_text("ROTATED-KEY-0042", encoding="utf-8")
    agent.configure_main("config", "UPDATE", _contents(None))
    assert agent.co2_signal.api_key == "ROTATED-KEY-0042"


@pytest.mark.parametrize("text", ["", " \n\t\n", "<your_electricity_maps_api_key>"], ids=repr)
def test_empty_or_placeholder_file_means_no_key(tmp_path, monkeypatch, caplog, recorded_requests, text):
    monkeypatch.setenv(ENV, str(_key_file(tmp_path, text)))
    agent, scheduled = _make_agent(tmp_path)
    with caplog.at_level(logging.INFO):
        # A usable config key must not rescue an empty key file.
        agent.configure_main("config", "NEW", _contents())

    assert [m for m in _messages(caplog) if m == OFF_LINE] == [OFF_LINE]
    assert agent.co2_signal is None
    assert [f for f in scheduled if "co2" in f.__name__] == []
    assert recorded_requests == []


def test_unreadable_path_logs_path_and_error_type_only(tmp_path, monkeypatch, caplog, capsys, recorded_requests):
    missing = tmp_path / "no-such-key-file"
    monkeypatch.setenv(ENV, str(missing))
    agent, scheduled = _make_agent(tmp_path)
    with caplog.at_level(logging.INFO):
        agent.configure_main("config", "NEW", _contents())

    messages = _messages(caplog)
    unreadable = [m for m in messages if m.startswith("Electricity Maps key file")]
    # The constructor's own NEW configure also reads the file, so the line may repeat.
    assert unreadable
    assert set(unreadable) == {"Electricity Maps key file %s unreadable: FileNotFoundError" % missing}
    assert OFF_LINE in messages
    assert agent.co2_signal is None
    assert [f for f in scheduled if "co2" in f.__name__] == []
    assert recorded_requests == []
    captured = capsys.readouterr()
    assert CONFIG_MARKER not in captured.out + captured.err
    assert all(CONFIG_MARKER not in m for m in messages)


def test_directory_path_is_unreadable_not_a_crash(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv(ENV, str(tmp_path))
    agent, _ = _make_agent(tmp_path)
    with caplog.at_level(logging.INFO):
        agent.configure_main("config", "NEW", _contents())
    assert agent.co2_signal is None
    assert any("unreadable: IsADirectoryError" in m for m in _messages(caplog))


def test_without_the_variable_the_config_key_works(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv(ENV, raising=False)
    agent, scheduled = _make_agent(tmp_path)
    with caplog.at_level(logging.DEBUG):
        agent.configure_main("config", "NEW", _contents())

    assert agent.co2_signal.api_key == CONFIG_MARKER
    assert len(scheduled) > 0
    assert IGNORED_LINE not in _messages(caplog)
    assert all(CONFIG_MARKER not in m for m in _messages(caplog))


def test_empty_variable_is_treated_as_unset(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "")
    agent, _ = _make_agent(tmp_path)
    agent.configure_main("config", "NEW", _contents())
    assert agent.co2_signal.api_key == CONFIG_MARKER
