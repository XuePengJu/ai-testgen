"""pytest 全局配置：把应用日志重定向到隔离目录，避免测试进程污染真实 logs/。

背景（M10.1 排查结论）：main.py 在 import 时调用 setup_logging()，默认相对路径
logs/ —— pytest 收集/执行阶段 import app.main 会把 60+ 次启动标记写进真实日志，
掩盖真实服务的滚动痕迹。此 conftest 在任何测试模块 import 之前设置 AITF_LOG_DIR，
logging_config.setup_logging() 读取该变量，测试日志统一落 /tmp/atgen-test-logs/。
"""
import os

os.environ.setdefault("AITF_LOG_DIR", "/tmp/atgen-test-logs")
