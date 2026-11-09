"""Marker trackers the exhibition can run: reacTIVision or Ritualvision.

Both write TUIO 1.1 /tuio/2Dobj, so everything after tracker/tuio.py is the same. Select
one with the `tracker` setting (web UI, or exhibition_start.bat --tracker).
"""

from tracker.engines.base import COMMON_DEFAULTS, TrackerEngine
from tracker.engines.reactivision import RTV_ONLY_DEFAULTS, ReactivisionEngine
from tracker.engines.ritualvision import RV_DEFAULTS, RitualvisionEngine

ENGINES = {e.name: e for e in (ReactivisionEngine(), RitualvisionEngine())}
TRACKERS = tuple(ENGINES)                     # ("reactivision", "ritualvision")

#: every tracker setting with its default (common + per engine)
TRACKER_DEFAULTS = {**COMMON_DEFAULTS, **RTV_ONLY_DEFAULTS, **RV_DEFAULTS}


def engine_name(cfg):
    name = (cfg or {}).get("tracker")
    return name if name in ENGINES else "reactivision"


def get_engine(cfg_or_name):
    name = cfg_or_name if isinstance(cfg_or_name, str) else engine_name(cfg_or_name)
    return ENGINES.get(name, ENGINES["reactivision"])


def tracker_exe(cfg, exe_override=""):
    """(engine name, exe path or None) for the configured tracker."""
    eng = get_engine(cfg)
    return eng.name, eng.find_exe(cfg, exe_override)


def engines_info():
    """For the web UI: which settings belong to which engine."""
    return [{"name": e.name, "label": e.label, "fields": list(e.fields)} for e in ENGINES.values()]
