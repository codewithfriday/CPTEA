"""Every test session gets its own data folder, set before any application module is imported."""
import os
import tempfile
from pathlib import Path

if "CPTEA_DATA_DIR" not in os.environ or not os.environ.get("CPTEA_KEEP_DATA_DIR"):
    _tmp = tempfile.mkdtemp(prefix="cptea_test_")
    os.environ["CPTEA_DATA_DIR"] = _tmp
    os.environ["CPTEA_STATIC_DIR"] = str(Path(_tmp) / "nostatic")
