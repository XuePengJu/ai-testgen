"""V1.3 提速回归：结构化抽取关思考 + 子任务并发后仍保持顺序与编号稳定。

背景（2026-10-11 实测）：7 个测试点的用例生成是**串行**的 7 次模型调用，
单次 42.9~57.8s，合计 353s，占整条任务 384s 的 92%；且生成链路当时**没有关思考**
（项目内既有结论：结构化抽取开思考「耗时翻数倍」且正文可能被截断成空）。

本文件锁死三件事：
1. 生成链路一律以 `enable_thinking=False` 调用模型；替身/极简客户端不认该参数时
   回落单参调用（否则既有测试替身会 TypeError）；
2. 并发只压缩墙钟耗时，**用例顺序与 TC 编号仍严格按 unit × role 原顺序**；
3. 并发度 = 1 时与旧的串行语义等价。
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generator_core"))

from src.generator.case_generator import (  # noqa: E402
    CaseGenerator,
    call_generate,
)
from src.models.testcase import RequirementUnit  # noqa: E402


def _unit(name: str) -> RequirementUnit:
    return RequirementUnit(name=name, description="描述", kind="business")


def _cases_json(name: str) -> str:
    return json.dumps([{
        "title": f"用例-{name}", "module": name, "case_type": "正向",
        "priority": "P1", "steps": ["步骤1"], "expected": "预期1",
    }], ensure_ascii=False)


class _LegacyClient:
    """只接受单个 prompt 的替身（模拟既有测试替身与极简注入客户端）。"""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return _cases_json(_name_of(prompt))


def _name_of(prompt: str) -> str:
    """从提示词里取回测试点名字（生成提示词的固定行）。"""
    for line in prompt.splitlines():
        if line.startswith("- 模块/名称："):
            return line.split("：", 1)[1].strip()
    return "未知"


class _ModernClient(_LegacyClient):
    """支持 enable_thinking 的真实客户端替身：记录收到的开关值。"""

    def __init__(self):
        super().__init__()
        self.thinking_flags: list = []

    def generate(self, prompt: str, enable_thinking=None) -> str:
        self.thinking_flags.append(enable_thinking)
        return super().generate(prompt)


class _ReverseDelayClient(_LegacyClient):
    """让序号越大的测试点越快返回 —— 制造「完成顺序 ≠ 声明顺序」。

    若实现按完成顺序收敛用例，顺序就会反过来，本文件的顺序断言即失守。
    """

    def generate(self, prompt: str, enable_thinking=None) -> str:
        name = _name_of(prompt)
        try:
            time.sleep(max(0.0, (6 - int(name[1:])) * 0.08))
        except (ValueError, IndexError):
            pass
        return super().generate(prompt)


# ============ 1. 关思考：传参 + 替身兼容 ============

class TestNoThinking:
    @pytest.mark.title("生成链路强制以 enable_thinking=False 调用模型")
    def test_passes_enable_thinking_false(self):
        client = _ModernClient()
        assert call_generate(client, "p") != ""
        assert client.thinking_flags == [False]

    @pytest.mark.title("客户端不认 enable_thinking 时回落单参调用，不抛 TypeError")
    def test_falls_back_for_legacy_client(self):
        client = _LegacyClient()
        assert call_generate(client, "p") != ""
        assert client.calls == 1

    @pytest.mark.title("生成用例全链路都走关思考（generate 内部生效）")
    def test_generator_uses_no_thinking(self):
        client = _ModernClient()
        CaseGenerator(client=client).generate([_unit("A"), _unit("B")])
        assert client.thinking_flags == [False, False]

    @pytest.mark.title("Mock 等可变关键字参数客户端仍被判定为支持关思考")
    def test_varkw_client_counts_as_supported(self):
        from unittest.mock import MagicMock

        client = MagicMock()
        client.generate.return_value = _cases_json("A")
        assert call_generate(client, "p") != ""
        assert client.generate.call_args.kwargs.get("enable_thinking") is False


# ============ 2. 并发：顺序与编号稳定性 ============

class TestConcurrentOrder:
    @pytest.mark.title("并发后用例顺序与 TC 编号仍按 unit 原顺序")
    def test_order_preserved_with_concurrency(self):
        names = [f"P{i}" for i in range(7)]
        units = [_unit(n) for n in names]
        # 靠后的测试点先返回：若实现按「完成顺序」收敛，顺序就会乱
        cases = CaseGenerator(client=_ReverseDelayClient()).generate(
            units, concurrency=4)
        assert [c.module for c in cases] == names
        assert [c.case_id for c in cases] == [f"TC-{i:03d}" for i in range(1, 8)]

    @pytest.mark.title("并发度 1 与串行语义等价（顺序、编号一致）")
    def test_concurrency_one_equals_serial(self):
        units = [_unit(n) for n in ("A", "B", "C")]
        serial = CaseGenerator(client=_LegacyClient()).generate(units, concurrency=1)
        parallel = CaseGenerator(client=_LegacyClient()).generate(units, concurrency=3)
        assert [c.module for c in serial] == [c.module for c in parallel]
        assert [c.case_id for c in serial] == [c.case_id for c in parallel]

    @pytest.mark.title("并发确实压缩墙钟耗时（7 × 0.3s 串行 2.1s → 并发 <1.2s）")
    def test_concurrency_is_faster(self):
        units = [_unit(f"P{i}") for i in range(7)]
        t0 = time.time()
        CaseGenerator(client=_LegacyClient(delay=0.3)).generate(units, concurrency=4)
        elapsed = time.time() - t0
        assert elapsed < 1.2, f"并发未生效，耗时 {elapsed:.2f}s（串行应为 2.1s）"

    @pytest.mark.title("进度回调按完成数递增且最终等于子任务总数")
    def test_progress_callback(self):
        seen: list[tuple] = []
        CaseGenerator(client=_LegacyClient()).generate(
            [_unit("A"), _unit("B"), _unit("C")],
            progress_cb=lambda cur, total, label, so_far, new: seen.append(
                (cur, total, so_far, len(new))),
            concurrency=3)
        assert [s[0] for s in seen] == [1, 2, 3]
        assert {s[1] for s in seen} == {3}
        assert seen[-1][2] == 3          # 累计用例数
        assert all(s[3] == 1 for s in seen)   # 每次回调带上本次新增用例

    @pytest.mark.title("关闭演示模式时，单个子任务抛错即冒泡（与串行语义一致）")
    def test_exception_propagates(self, monkeypatch):
        from src.generator import case_generator as cg_mod

        # conftest 为存量用例默认开了演示模式；这里显式关掉，断言「真实失败必须暴露」
        monkeypatch.setattr(cg_mod.settings, "ALLOW_DEMO", False)

        class _Boom:
            def generate(self, prompt, enable_thinking=None):
                raise RuntimeError("模型炸了")

        with pytest.raises(RuntimeError):
            CaseGenerator(client=_Boom()).generate(
                [_unit("A"), _unit("B")], concurrency=2)

    @pytest.mark.title("演示模式下失败降级为模拟用例，不抛错（既有行为不变）")
    def test_demo_mode_still_falls_back(self, monkeypatch):
        from src.generator import case_generator as cg_mod

        monkeypatch.setattr(cg_mod.settings, "ALLOW_DEMO", True)

        class _Boom:
            def generate(self, prompt, enable_thinking=None):
                raise RuntimeError("模型炸了")

        cases = CaseGenerator(client=_Boom()).generate([_unit("A")], concurrency=1)
        assert cases, "演示模式应降级为规则化用例，且不得抛错"
