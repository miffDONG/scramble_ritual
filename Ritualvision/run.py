import os
import sys

# camera probing makes OpenCV print a warning per backend and index; keep real errors only
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

from ritualvision.main import main

if __name__ == "__main__":
    sys.exit(main())
