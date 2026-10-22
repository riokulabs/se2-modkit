import subprocess
import sys

import se2modkit


def test_import_has_no_heavy_deps():
    code = "import sys, se2modkit; assert 'numpy' not in sys.modules and 'bpy' not in sys.modules"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_version_string():
    assert se2modkit.__version__ == "0.1.0.dev0"
    assert se2modkit.SUPPORTED_GAME == "4.0.0.3155"
