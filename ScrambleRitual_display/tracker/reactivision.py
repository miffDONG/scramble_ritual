"""Compatibility module: the tracker code now lives in

  tracker/tuio.py                    TUIO receiver, ExhibitObject (shared)
  tracker/process.py                 tracker child process (shared)
  tracker/engines/base.py            camera.xml, camera modes, engine interface
  tracker/engines/reactivision.py    reacTIVision
  tracker/engines/ritualvision.py    Ritualvision

Existing `from tracker.reactivision import ...` keeps working through the names below.
New code should import from the modules above.
"""

from tracker.engines import TRACKER_DEFAULTS as RTV_DEFAULTS, TRACKERS, tracker_exe  # noqa: F401
from tracker.engines.base import (  # noqa: F401
    SETTING_KEYS as _SETTING_KEYS, build_camera_xml, default_mode, resolve_mode,
)
from tracker.engines.reactivision import (  # noqa: F401
    EXE_CANDIDATES, INSTALL_DIR, MODES_FILE, RTV_WORK_DIR, build_reactivision_xml,
    ensure_local_install, find_exe, import_xml, list_modes, original_xml_paths,
    parse_list_output, tree_attr, write_configs,
)
from tracker.engines.ritualvision import (  # noqa: F401
    RITUALVISION_EXE, find_ritualvision, is_ritualvision,
)
from tracker.process import (  # noqa: F401
    ReactivisionProcess, TrackerProcess, parse_stdout_line as _parse_stdout_line,
)
from tracker.tuio import ExhibitObject, TuioReceiver, tuio_angle_deg  # noqa: F401
