"""测试共用夹具与路径引导。

无论以 ``python3 -m unittest discover -s tests`` 还是隔离启动器
``python3 -I tests/run_tests.py`` 运行，本模块先把项目根加入 sys.path，
使 ``wageband`` 包在隔离模式下也可导入。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wageband.model import load_dataset  # noqa: E402

SAMPLES = ROOT / "samples"


class DatasetCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(SAMPLES)
        cls.employees = {e.worker: e for e in cls.dataset.employees}
