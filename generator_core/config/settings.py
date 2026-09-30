"""配置读取：百炼 Key、模型、mock 模式判断。"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录（config/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
BAILIAN_MODEL = os.getenv("BAILIAN_MODEL", "qwen-plus").strip()


def is_mock() -> bool:
    """未配置百炼 Key 时返回 True，走本地规则生成（无需联网）。"""
    return not bool(DASHSCOPE_API_KEY)


# 演示内容开关：默认关（0）。关闭时未注入模型 → 报错，不静默出假用例。
# 设为 1 恢复旧演示行为（本地/现场演示用）。
ALLOW_DEMO = os.getenv("AITF_ALLOW_DEMO", "0") == "1"


# Prompt 模板目录
PROMPTS_DIR = BASE_DIR / "config" / "prompts"
