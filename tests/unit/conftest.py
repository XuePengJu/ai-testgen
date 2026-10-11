"""unit 层 conftest —— 保持为空（不设任何 autouse 夹具），这是「秒回」的关键（D8）。

- 这 13/17 个纯函数单测与 api/ 的 session 级 ``accounts`` 夹具完全隔离：
  unit 用例不再被迫承担「起 TestClient + 建库 + 注册账号」的成本。
- 需要的 env 隔离与 ``sys.path`` 引导已由**仓库根 conftest.py** 提供，
  故本层无需再放任何夹具或路径代码（根 conftest 对所有层恒被加载）。
"""
