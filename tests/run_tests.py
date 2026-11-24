"""隔离模式测试启动器：显式加入项目根与测试目录后运行全部测试。

用法（项目根目录）：
    python3 -I tests/run_tests.py -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS), top_level_dir=str(TESTS))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
