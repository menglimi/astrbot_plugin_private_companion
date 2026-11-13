"""Route only this plugin's domain logs through AstrBot's plugin log broker."""

from copy import copy
import logging

from astrbot.core.log import LogManager

from .log_buffer import install_log_buffer


PLUGIN_NAME = "astrbot_plugin_private_companion"
_STATE = "_private_companion_v2_log_bridge"


class PluginLogBridge(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        if self.sink.isEnabledFor(record.levelno):
            forwarded = copy(record)
            forwarded.name = self.sink.name
            self.sink.handle(forwarded)


def _names(entry_name):
    namespace = entry_name.rsplit(".", 1)[0]
    return tuple(dict.fromkeys(("companion_v2", "astrbot_host", namespace + ".astrbot_host")))


def install_logging_bridge(entry_name):
    sink = LogManager.get_plugin_logger(PLUGIN_NAME)
    install_log_buffer(sink)
    for name in _names(entry_name):
        source = logging.getLogger(name)
        prior = getattr(source, _STATE, None)
        if prior is not None:
            source.removeHandler(prior[0])
            prior[0].close()
            original_level, original_propagate = prior[1:3]
        else:
            original_level, original_propagate = source.level, source.propagate
        handler = PluginLogBridge(sink)
        source.addHandler(handler)
        # The sink checks its current level on every record, including changes
        # made in the Dashboard after this bridge was installed.
        source.setLevel(logging.DEBUG)
        source.propagate = False
        setattr(source, _STATE, (handler, original_level, original_propagate, entry_name))


def retire_logging_bridge(entry_name):
    for name in _names(entry_name):
        source = logging.getLogger(name)
        state = getattr(source, _STATE, None)
        if state is None or state[3] != entry_name:
            continue
        source.removeHandler(state[0])
        state[0].close()
        source.setLevel(state[1])
        source.propagate = state[2]
        delattr(source, _STATE)
