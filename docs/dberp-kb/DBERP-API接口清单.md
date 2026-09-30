# DBERP API 接口清单（V 1.3）

> 来源：SSH 只读读取 `module/Api/config/module.config.php` 路由 + `IndexController.php` 源码。
> 用途：接口自动化测试参考（主要针对商城订单同步场景）。

---

## 1. 接口概览

DBERP 的 Api 模块是**商城对接专用接口**（供 DBShop / DBCart / 其他商城系统同步订单到 ERP），不是通用业务 CRUD API。

| 路由 | 控制器 | 说明 |
|---|---|---|
| `/api[/:action]` | Api\Controller\IndexController | **唯一有效入口**，单 action `indexAction` 按 `appType` + `action` 动态分发 |
| `/other-api[/:action]` | Api\Controller\OtherController | **空壳**，控制器无任何 action 方法，实际不提供接口 |

> ⚠️ `OtherController.php` 仅有构造函数、无 action，因此 `/other-api/*` 无实际处理逻辑；真实接口全部走 `/api`。

---

## 2. 认证方式

请求方式：POST，表单字段由 `ApiForm` 校验。

- 公共字段：`appId`（应用标识）、`action`（动作名）、`dataStr`（业务数据密文）、`sign`（仅「其他商城」类型需要）。
- 应用类型判定：查 `dberp_app` 表，`app_access_id = appId` 且 `app_state = 1`。
- **DBShop / DBCart / 珑大极简商城**（`app_type` ∈ {dbshop, dbcart, shop}）：
  - `dataStr` 用 `Laminas\Crypt\BlockCipher`（openssl）以 `appAccessSecret` 为密钥**加密**，服务端用同一密钥解密。
- **其他商城系统**（app_type 为其他值）：
  - 需传 `sign = md5(dataStr + appAccessSecret)`，服务端校验一致才解密 `dataStr`。
- 校验失败返回：`{"code":404,"status":"error","message":"该账户不存在"/"sign不一致","result":[]}`。

---

## 3. 业务动作（action）

仅当 `action` 命中对应白名单数组且 `dataStr` 非空时才执行。

### 3.1 DBShop / DBCart / 商城类型（dbshopActionArray）
| action | 含义 | 对应业务 |
|---|---|---|
| addOrder | 添加商城订单 | 商城订单同步进 ERP（shop_order） |
| cancelOrder | 取消订单 | 取消同步订单 |
| deleteOrder | 删除订单 | 删除同步订单 |
| dbshop3PaymentOrder | 支付 order（DBShop3） | 标记支付 |
| paymentOrder | 支付 order | 标记支付 |
| dbshop3DeliverOrder | 发货 order（DBShop3） | 标记发货 |
| deliverOrder | 发货 order | 标记发货 |
| finishOrder | 完成 order | 标记完成 |

### 3.2 其他商城类型（otherActionArray）
| action | 含义 |
|---|---|
| otherAddOrder | 添加订单 |
| otherCancelOrder | 取消订单 |
| otherDeleteOrder | 删除订单 |
| otherPaymentOrder | 支付订单 |
| otherDeliverOrder | 发货订单 |
| otherFinishOrder | 完成订单 |

---

## 4. 返回结构

统一 JSON（ViewJsonStrategy）：
```json
{ "code": 200, "status": "success", "message": "...", "result": [...] }
```
异常/未授权：`code=404, status=error`。

---

## 5. 接口自动化测试建议

- **正向**：构造合法 `appId` + 正确密文，验证 addOrder→paymentOrder→deliverOrder→finishOrder 全链路状态流转（对应 `shop_order` 表状态）。
- **安全**：
  - 错误 `appId` → 返回「该账户不存在」；
  - 「其他商城」类型缺 `sign` 或 `sign` 错误 → 返回「sign不一致」；
  - `BlockCipher` 密钥错误 → 解密失败，应不落单。
- **越权**：用已禁用应用（app_state≠1）→ 不处理。
- **参数**：`action` 不在白名单 → 不执行（静默或无操作）。
- 注意：接口直接操作 `ShopOrder` 实体及关联商品/地址，测试需准备对应的 `dberp_app` 应用记录与商城商品绑定。
