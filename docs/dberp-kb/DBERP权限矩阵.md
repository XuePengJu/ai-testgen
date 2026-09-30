# DBERP 权限矩阵（V 1.3）

> 来源：SSH 只读读取各模块 `config/permission.php`（Admin/Company/Customer/Extend/Finance/Purchase/Report/Sales/Shop/Stock/Store 共 11 个模块）。
> 用途：权限测试用例设计、RBAC 模型说明、RAG 权限类问题答疑。

---

## 1. 权限机制

- 权限存储：管理员组表 `dberp_admin_group.admin_group_purview`（TEXT），内容为**逗号分隔的权限标识字符串**。
- 权限标识格式：`控制器名_方法名`（`str_replace('\\','_', $controllerName) . '_' . $actionName`，即把命名空间反斜杠换成下划线）。
- 鉴权：`Admin\Service\AuthManager` 在请求时取出当前管理员所属组的 `admin_group_purview`，拆成数组；若当前 `控制器_方法` 不在数组内则拦截。
- **内置超级组**：`admin_group_id = 1`（名为「管理员」的安装初始组）**跳过权限校验**，且后台禁止删除/编辑其权限（见使用文档 9.2）。
- 其他账号：`admin_group_id != 1` 时按 `admin_group_purview` 数组校验，空数组 = 无任何权限。

---

## 2. 权限项清单（按模块）

### 2.1 系统（Admin 模块）
| 菜单 | 权限项（action） | 可读名称 |
|---|---|---|
| 系统设置 | SystemController::index | 系统设置 |
| 管理员 | AdminController::index/add/edit/delete/changePassword | 列表/添加/编辑/删除/修改密码 |
| 管理员组 | AdminGroupController::adminGroupList/addAdminGroup/editAdminGroup/deleteAdminGroup | 列表/添加/编辑/删除 |
| 地区管理 | RegionController::index/add/edit/delete | 列表/添加/编辑/删除 |
| 商城绑定 | AppController::index/add/edit/delete | 列表/添加/编辑/删除 |
| 服务绑定 | ServiceBindController::index/clearServiceBind | 服务绑定/服务解绑 |
| 打印模板 | PrintTemplateController::index/edit | 列表/设置 |
| 系统更新 | UpdateController::index/updateErpPackageInfo/startErpPackage | 更新包列表/详情/更新 |
| 操作日志 | OperLogController::index/clearOperLog | 查看日志/删除日志 |

### 2.2 基础数据（Store 模块）
| 菜单 | 权限项 | 可读名称 |
|---|---|---|
| 商品 | GoodsController::index/add/importGoods/edit/delete/priceTrend/goodsWarehouse | 列表/添加/批量导入/编辑/删除/价格趋势/入库分布 |
| 商品分类 | GoodsCategoryController::index/add/edit/delete/updateAll/addAjaxCategory | 列表/添加/编辑/删除/批量操作/其他页添加 |
| 商品品牌 | BrandController::index/add/edit/delete/updateAll | 列表/添加/编辑/删除/批量操作 |
| 计量单位 | UnitController::index/add/edit/delete/addAjaxUnit | 列表/添加/编辑/删除/其他页添加 |
| 仓库 | WarehouseController::index/add/edit/delete/updateAll/addAjaxWarehouse | 列表/添加/编辑/删除/批量操作/其他页添加 |
| 商品序列号 | GoodsSerialNumberController::index | 序列号列表 |
| 物流公司 | LogisticsController::index/add/edit/delete/addAjaxLogistics | 列表/添加/编辑/删除/其他页添加 |
| 仓位 | PositionController（**代码中被注释禁用**） | 未启用 |

### 2.3 客户（Customer 模块）
| 菜单 | 权限项 | 可读名称 |
|---|---|---|
| 客户 | CustomerController::index/add/edit/delete/importCustomer/exportCustomerData/addAjaxCustomer | 列表/添加/编辑/删除/批量导入/导出/其他页添加 |
| 客户分类 | CustomerCategoryController::index/add/edit/delete/addAjaxCustomerCategory | 列表/添加/编辑/删除/其他页添加 |
| 供应商 | SupplierController::index/add/edit/delete/importSupplier/exportSupplierData/addAjaxSupplier | 列表/添加/编辑/删除/批量导入/导出/其他页添加 |
| 供应商分类 | SupplierCategoryController::index/add/edit/delete/addAjaxSupplierCategory | 列表/添加/编辑/删除/其他页添加 |

### 2.4 采购（Purchase 模块）
| 菜单 | 权限项 | 可读名称 |
|---|---|---|
| 采购订单 | OrderController::index/add/edit/delete/view/authPassOrder/cancelOrder/delOrderGoods | 列表/添加/编辑/删除/查看/审核/取消/删商品 |
| 采购入库单 | WarehouseOrderController::index/add/view/delete/insertWarehouse | 列表/添加/查看/删除/待入库单入库 |
| 采购退货单 | OrderReturnController::index/view/cancel/returnFinish/returnOrder | 列表/查看/取消/退货完成/添加退货 |

### 2.5 销售（Sales 模块）
| 菜单 | 权限项 | 可读名称 |
|---|---|---|
| 销售订单 | SalesOrderController::index/add/edit/view/delete/delSalesOrderGoods/confirmSalesOrder/cancelSalesOrder/sendOrder | 列表/添加/编辑/查看/删除/删商品/确认/取消确认/发货 |
| 销售发货单 | SalesSendOrderController::index/view/finishSalesOrder | 列表/查看/确认收货 |
| 销售退货单 | SalesOrderReturnController::index/add/view/finishInWarehouse/finish/cancel | 列表/添加/查看/退货完成入库/完成不入库/取消 |

### 2.6 库存（Stock 模块）
| 菜单 | 权限项 | 可读名称 |
|---|---|---|
| 商品库存 | GoodsStockController::index/view | 列表/查看 |
| 其他入库 | IndexController::index/add/view | 列表/添加/查看 |
| 其他出库 | ExWarehouseController::index/add/view | 列表/添加/查看 |
| 库存盘点 | StockCheckController::index/add/edit/delete/confirm/view/delStockCheckGoods | 列表/添加/编辑/删除/确认/查看/删商品 |
| 库间调拨 | StockTransferController::index/add/view/delete/authPassStockTransfer | 列表/添加/查看/删除/审核调拨 |
| 库存预警 | StockWarningController::index | 预警商品列表 |

### 2.7 财务（Finance 模块）
| 菜单 | 权限项 | 可读名称 |
|---|---|---|
| 应付账款 | PayableController::index/addPayable/show/payableLog | 列表/添加付款/详情/付款记录 |
| 应收账款 | ReceivablesController::index/addReceivable/show/receivableLog | 列表/添加收款/详情/收款记录 |

### 2.8 报表（Report 模块）
| 菜单 | 权限项 | 可读名称 |
|---|---|---|
| 库存报表 | ReportStockController::index/exportStockData | 查看/导出 |
| 更多报表 | IndexController::index | 查看更多报表 |

### 2.9 商城（Shop 模块）
| 菜单 | 权限项 | 可读名称 |
|---|---|---|
| 商城订单 | IndexController::index/view/delete | 列表/查看/删除 |
| 订单商品 | OrderGoodsController::index/distributionGoods/finishDistribution | 列表/匹配商品/商品补货 |

### 2.10 扩展（Extend 模块）
| 菜单 | 权限项 | 可读名称 |
|---|---|---|
| 扩展插件 | IndexController::index/pluginList | 已安装列表/可安装列表 |

> Company 模块 `permission.php` 返回空数组 `return []`，无独立权限项（企业基础信息归入系统设置）。

---

## 3. 权限测试要点

1. **越权拦截**：用仅授权「采购订单列表」的账号尝试「审核订单」(authPassOrder)，应被拦截。
2. **内置组保护**：admin_group_id=1 的「管理员」组在编辑页应禁用删除/权限修改。
3. **空权限组**：新建组不勾选任何权限，其下账号登录后所有业务菜单不可访问。
4. **权限粒度**：权限精确到「方法」级（如列表可见但删除不可见），需逐项核对。
