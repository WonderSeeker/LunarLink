# -*- coding: utf-8 -*-
"""SUT 启动入口。

用法（svc.ps1 与 CI 都用这个）：
    cd D:\\Projects\\LunarLink\\sut
    D:\\Projects\\LunarLink\\backend\\.venv\\Scripts\\python.exe run.py

或用 svc.ps1：cd D:\\Projects ; .\\svc.ps1 start sut
访问：http://127.0.0.1:8021/health
"""

import os
import sys

# 让 `import sut.xxx` 可用：把仓库根目录（sut 的父目录）放进 sys.path。
# 这样无论从哪个 CWD 启动都不会 ImportError。
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import uvicorn  # noqa: E402

from sut import config  # noqa: E402
from sut.app import app  # noqa: E402


def main() -> None:
    print(
        "[sut] starting on http://{}:{}/  (db={}, test_hooks={})".format(
            config.HOST,
            config.PORT,
            config.DB_PATH,
            config.ENABLE_TEST_HOOKS,
        ),
        flush=True,
    )
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
