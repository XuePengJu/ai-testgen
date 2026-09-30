"""测试方法论策略（把测试思维编码进生成过程，而非让 AI 自由发挥）。"""

STRATEGIES = {
    "equivalence": "等价类划分：将每个输入划分为有效等价类和无效等价类，每类至少一条用例。",
    "boundary": "边界值分析：对每个数值/长度输入取 min、min-1、min+1、max、max-1、max+1。",
    "scenario": "场景法：梳理基本流与备选流，覆盖核心业务状态机与异常分支。",
    "error_guessing": "错误猜测：基于经验猜测易错点（越权、并发、幂等、签名篡改、超卖）。",
}


def list_strategies() -> list[str]:
    return list(STRATEGIES.keys())


def strategy_text(kind: str) -> str:
    return STRATEGIES.get(kind, "")
