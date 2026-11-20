"""Tests for the publish_price_signal RPC (issue #11).

The RPC starts one price run in its own greenlet and returns the number of
hourly prices it will publish: 24 for a valid TOU config, 0 when no valid
price config is loaded, in which case nothing is published.
"""
import types

import gevent

from derhost.compat.import_hook import install_volttron_compatibility

install_volttron_compatibility()

import grid_signals_agent.agent as agent_module  # noqa: E402 (must follow the compat install)

PRICE_TOPIC = "devices/PNNL/grid_information/price/all"
TIERS = {"off-peak": 0.13246, "mid-peak": 0.15878, "on-peak": 0.19598}


def _tou_contents():
    return {
        "type_of_grid_service_signals": {
            "price": {
                "type_of_price_signal": "TOU",
                "TOU_pricing": {
                    "interval": {
                        "off-peak": [[0, 7], [21, 23]],
                        "mid-peak": [[7, 10], [18, 21]],
                        "on-peak": [[10, 18]],
                    },
                    "pricing": dict(TIERS),
                },
            }
        }
    }


class _Result:
    def get(self, timeout=None):
        return None


def _make_agent():
    """Bare instance with configure_main's and publish_data's dependencies
    seeded; the constructor is bypassed as in the sibling tests."""
    agent = object.__new__(agent_module.GridSignalAgent)
    agent.default_config = {
        "campus": "PNNL",
        "run_dayahead_schedule": "0 0 * * *",
        "run_realtime_schedule": "0 * * * *",
    }
    agent.localtz = "UTC"
    agent.price_signals = None
    agent.grid_service_signals = None
    agent.co2_config = None
    agent.published = []
    agent.scheduled = []

    def publish(peer, topic, headers=None, message=None):
        agent.published.append((topic, headers, message))
        return _Result()

    agent.core = types.SimpleNamespace(
        identity="test-agent",
        schedule=lambda *a, **k: agent.scheduled.append(a),
    )
    agent.vip = types.SimpleNamespace(pubsub=types.SimpleNamespace(publish=publish))
    return agent


def _configure(agent, contents):
    agent.configure_main("config", "NEW", contents)
    agent.scheduled.clear()


def test_publish_price_signal_is_an_rpc_export():
    method = agent_module.GridSignalAgent.publish_price_signal
    assert method.rpc_exported is True


def test_valid_tou_config_returns_24_and_publishes_24():
    agent = _make_agent()
    _configure(agent, _tou_contents())

    returned = agent.publish_price_signal()

    # The RPC returns before the greenlet it spawned has run.
    assert returned == 24
    assert agent.published == []

    gevent.sleep(0.2)

    assert len(agent.published) == 24
    for topic, headers, message in agent.published:
        assert topic == PRICE_TOPIC
        assert isinstance(message[0]["tou"], float)
        assert message[0]["tou"] in TIERS.values()
        assert message[1]["tou"]["type"] == "float"
        assert "Date" in headers


def test_no_price_config_returns_0_and_publishes_nothing():
    agent = _make_agent()
    # Control: with no price block, configure_main schedules no price job.
    agent.configure_main("config", "NEW", {"type_of_grid_service_signals": {}})
    agent.price_signals = {}

    returned = agent.publish_price_signal()
    gevent.sleep(0.2)

    assert returned == 0
    assert agent.published == []


def test_unusable_tou_block_returns_0_and_publishes_nothing():
    agent = _make_agent()
    contents = _tou_contents()
    contents["type_of_grid_service_signals"]["price"]["TOU_pricing"] = {}
    _configure(agent, contents)

    returned = agent.publish_price_signal()
    gevent.sleep(0.2)

    assert returned == 0
    assert agent.published == []
