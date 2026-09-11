"""pytest 全局隔离与状态清理。

1) 数据目录隔离 —— **必须在任何 utils 导入之前执行**
----------------------------------------------------
`utils/logger.py` 在 import 时就按 `LOGS_FOLDER` 建好 FileHandler：

    from utils.static import ... LOGS_FOLDER ...
    f_handler = logging.FileHandler(os.path.join(LOGS_FOLDER, "<时间戳>.log"))

而 `LOGS_FOLDER` 是 import 时绑定的模块级常量，事后 patch `static.LOGS_FOLDER`
只影响新读 static 的代码，**已建好的 handler 仍指着真实 logs/**。
后果：每跑一次 pytest 就往项目真实 `logs/` 丢一个文件。

conftest.py 在测试模块被收集之前导入，此时 utils 尚未导入，所以这里设置
`STEAMAUTO_BASE_DIR`（utils.static 会读它）能真正生效。

2) runtime 全局事件清理（autouse）
----------------------------------
`utils.runtime` 的 shutdown/wake 是模块级全局状态。任一测试请求过关停后若没清干净，
后续测试就会受牵连（典型症状：cloud_service 的后台线程一启动就发现 shutdown 已置位
而立即退出）。每个测试前后清零，消除顺序依赖。
"""

import os
import tempfile

import pytest

# 不覆盖调用方已有的设置（例如手工指定了隔离目录）
os.environ.setdefault("STEAMAUTO_BASE_DIR", tempfile.mkdtemp(prefix="sa-pytest-"))


@pytest.fixture(autouse=True)
def _reset_runtime_events():
    from utils import runtime

    runtime.clear_shutdown()
    runtime.clear_wake()
    yield
    runtime.clear_shutdown()
    runtime.clear_wake()
