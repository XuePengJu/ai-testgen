"""本地规则生成（mock 模式）：未配置百炼 Key 时，也能产出结构化、可评审的用例。

V2.7 增强：每条用例的 steps 与 step_expectations 一一对应，保证每个步骤都有预期结果。
"""
from src.models.testcase import TestCase, CaseType, Priority, RequirementUnit


def mock_generate(unit: RequirementUnit) -> list[TestCase]:
    """根据测试单元类型（api / module）生成覆盖 正向/异常/边界/场景 的用例。"""
    cases: list[TestCase] = []
    name = unit.name

    if unit.kind == "api":
        module = "订单对接API"
        cases += [
            TestCase(title="合法请求-正向下单", module=module, case_type=CaseType.POSITIVE, priority=Priority.P0,
                     pre_condition="后台已创建 App 且 appState=1，secret 正确",
                     steps=["构造合法 dataStr（订单 JSON）", "计算 sign=md5(dataStr+secret)", "POST /other-api 携带 appId/action/dataStr/sign"],
                     step_expectations=["dataStr 为合法订单 JSON，构造无报错", "sign 计算正确，与约定一致", "返回 code=200 status=success，ERP 生成对应订单"],
                     expected="返回 code=200 status=success，ERP 生成对应订单", test_data="正常订单参数"),
            TestCase(title="非法 appId-应用不存在/未启用", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     pre_condition="使用一个不存在或未启用的 appId",
                     steps=["携带错误 appId 发送请求"],
                     step_expectations=["返回 code=404，提示账户不存在，不落地订单"],
                     expected="返回 code=404，提示账户不存在，不落地订单", test_data="appId=invalid"),
            TestCase(title="sign 签名篡改-校验失败", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     pre_condition="正常 appId，但 dataStr 被篡改后未重算 sign",
                     steps=["修改 dataStr 后不重新计算 sign", "发送请求"],
                     step_expectations=["请求可正常发出", "返回 sign 不一致，拒绝处理"],
                     expected="返回 sign 不一致，拒绝处理", test_data="sign 错误"),
            TestCase(title="必填缺失-appId 为空", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     steps=["不传 appId 发送请求"],
                     step_expectations=["参数校验失败，返回错误"],
                     expected="参数校验失败，返回错误", test_data="appId 空"),
            TestCase(title="不合法 action-拒绝执行", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     steps=["action 传入不存在的动作名"],
                     step_expectations=["拒绝执行，返回错误"],
                     expected="拒绝执行，返回错误", test_data="action=unknown"),
            TestCase(title="边界-购买数量为 0/负数", module=module, case_type=CaseType.BOUNDARY, priority=Priority.P2,
                     steps=["goodsBuyNum 传 0 或 -1"],
                     step_expectations=["校验失败或被拦截"],
                     expected="校验失败或被拦截", test_data="goodsBuyNum=-1"),
            TestCase(title="边界-超长字符串字段", module=module, case_type=CaseType.BOUNDARY, priority=Priority.P2,
                     steps=["buyName 传 1000 字符"],
                     step_expectations=["长度校验或被截断处理"],
                     expected="长度校验或被截断处理", test_data="超长输入"),
            TestCase(title="场景-下单→支付→发货→完成 状态机", module=module, case_type=CaseType.SCENARIO, priority=Priority.P0,
                     pre_condition="订单已通过接口写入 ERP",
                     steps=["otherAddOrder 下单", "otherPaymentOrder 支付", "otherDeliverOrder 发货", "otherFinishOrder 完成"],
                     step_expectations=["下单成功，生成待支付订单", "支付成功，订单状态流转为已支付", "发货成功，订单状态流转为已发货", "完成成功，订单状态流转为已完成，库存/财务联动正确"],
                     expected="状态严格按序流转，最终订单完成且库存/财务联动正确", test_data="全链路"),
            TestCase(title="场景-重复 orderSn 幂等", module=module, case_type=CaseType.SCENARIO, priority=Priority.P1,
                     steps=["用相同 orderSn 重复调用 otherAddOrder 两次"],
                     step_expectations=["第一次下单成功", "第二次不重复生成 ERP 订单（幂等）"],
                     expected="第二次不重复生成 ERP 订单（幂等）", test_data="重复 orderSn"),
        ]
    else:  # module（业务需求）
        module = name
        cases += [
            TestCase(title="主流程-正向操作", module=module, case_type=CaseType.POSITIVE, priority=Priority.P0,
                     pre_condition="登录具备该模块权限的账号",
                     steps=["进入模块", "完成核心正向操作"],
                     step_expectations=["正常进入模块页面", "操作成功，数据正确落库"],
                     expected="操作成功，数据正确落库", test_data="正常数据"),
            TestCase(title="必填项缺失-提交拦截", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     steps=["关键字段留空提交"],
                     step_expectations=["前后端校验拦截，提示必填"],
                     expected="前后端校验拦截，提示必填", test_data="空字段"),
            TestCase(title="越权访问-无权限角色", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     pre_condition="使用无该模块权限的账号",
                     steps=["直接访问模块 URL / 操作"],
                     step_expectations=["被拦截或提示无权限"],
                     expected="被拦截或提示无权限", test_data="越权账号"),
            TestCase(title="业务规则-库存不为负", module=module, case_type=CaseType.BOUNDARY, priority=Priority.P0,
                     steps=["出库数量大于当前可用库存"],
                     step_expectations=["拦截，不允许负库存"],
                     expected="拦截，不允许负库存", test_data="超库存出库"),
            TestCase(title="业务规则-金额对账一致", module=module, case_type=CaseType.SCENARIO, priority=Priority.P1,
                     steps=["完成一笔业务", "核对库存/财务/报表三方数据"],
                     step_expectations=["业务操作成功，无异常", "库存/财务/报表三方数量与金额一致"],
                     expected="数量与金额三方一致", test_data="对账"),
            TestCase(title="边界-金额精度/超大值", module=module, case_type=CaseType.BOUNDARY, priority=Priority.P2,
                     steps=["金额输入超长小数或超大值"],
                     step_expectations=["精度控制或拦截"],
                     expected="精度控制或拦截", test_data="金额边界"),
            TestCase(title="状态机-非法流转", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     steps=["跳过中间状态直接完成/回退"],
                     step_expectations=["状态机拒绝非法跳转"],
                     expected="状态机拒绝非法跳转", test_data="状态越级"),
        ]
    return cases
