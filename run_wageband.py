#!/usr/bin/env python3
"""隔离模式启动器：显式加入项目根目录后调用 wageband CLI。

用 ``python3 -I run_wageband.py ...`` 运行；-I 下解释器不会把
当前目录、PYTHONPATH、用户 site-packages 加入路径，因此数据目录中
即使存在同名模块（如 json.py）也不会被加载。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wageband.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
