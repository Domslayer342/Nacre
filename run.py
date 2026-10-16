#!/usr/bin/env python3
import faulthandler
import os
import sys

from nacre.config import CACHE_DIR

# native crashes (Chromium / Qt aborts) leave a Python stack here instead of vanishing
faulthandler.enable(open(os.path.join(CACHE_DIR, "crash.log"), "a"), all_threads=True)

from nacre.app import main  # noqa: E402

sys.exit(main())
