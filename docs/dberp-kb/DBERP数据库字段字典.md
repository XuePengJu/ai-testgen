# DBERP 数据库字段字典（V 1.3，库 `dberp`，共 55 张表）

> 来源：SSH 只读拉取 `information_schema.columns`（字段名 / 类型 / 可空 / 键 / 默认值 / 注释），按表分组。
> 用于 RAG 知识库精确答疑（如「某字段含义 / 类型 / 是否主键」）。

> 表总数：**55**


## dberp_accounts_receivable

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| receivable_id | int(11) | NO | PRI | NULL |  |
| sales_order_id | int(11) | NO | MUL | NULL |  |
| sales_order_sn | varchar(50) | NO |  | NULL |  |
| send_order_id | int(11) | NO |  | NULL |  |
| send_order_sn | varchar(50) | NO |  | NULL |  |
| customer_id | int(11) | NO |  | NULL |  |
| customer_name | varchar(100) | NO |  | NULL |  |
| receivable_code | varchar(20) | NO |  | NULL |  |
| receivable_amount | decimal(19,4) | NO |  | 0.0000 |  |
| finish_amount | decimal(19,4) | NO |  | 0.0000 |  |
| sales_invoice_id | int(11) | YES | MUL | 0 |  |
| add_time | int(10) | NO |  | NULL |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_accounts_receivable_log

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| receivable_log_id | int(11) | NO | PRI | NULL |  |
| receivable_id | int(11) | NO | MUL | NULL |  |
| receivable_log_amount | decimal(19,4) | NO |  | 0.0000 |  |
| receivable_log_user | varchar(100) | NO |  | NULL |  |
| receivable_log_time | int(10) | NO |  | NULL |  |
| receivable_file | varchar(255) | YES |  | NULL |  |
| receivable_info | varchar(255) | YES |  | NULL |  |
| receivable_add_time | int(10) | NO |  | NULL |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_admin

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| admin_id | int(11) | NO | PRI | NULL |  |
| admin_group_id | int(11) | NO | MUL | NULL |  |
| admin_name | varchar(100) | NO | MUL | NULL |  |
| admin_passwd | varchar(72) | NO |  | NULL |  |
| admin_email | varchar(100) | NO |  | NULL |  |
| admin_state | tinyint(2) | NO | MUL | 1 |  |
| admin_add_time | int(10) | NO |  | NULL |  |
| admin_old_login_time | int(10) | YES |  | NULL |  |
| admin_new_login_time | int(10) | YES |  | NULL |  |
| admin_remark | varchar(300) | YES |  | NULL |  |

## dberp_admin_group

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| admin_group_id | int(11) | NO | PRI | NULL |  |
| admin_group_name | varchar(200) | NO |  | NULL |  |
| admin_group_purview | text | YES |  | NULL |  |
| admin_menu_state | tinyint(1) | YES |  | 0 |  |
| admin_menu_body | text | YES |  | NULL |  |

## dberp_app

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| app_id | int(11) | NO | PRI | NULL |  |
| app_name | varchar(100) | NO |  | NULL |  |
| app_access_id | varchar(30) | NO | MUL | NULL |  |
| app_access_secret | varchar(50) | NO |  | NULL |  |
| app_url | varchar(100) | NO |  | NULL |  |
| app_url_port | varchar(10) | NO |  | 80 |  |
| app_type | varchar(20) | NO |  | NULL |  |
| app_goods_bind_type | varchar(20) | YES |  | NULL | 商品绑定类型 |
| app_goods_bind | tinyint(1) | NO |  | 0 | 是否启用商品绑定 |
| app_goods_warehouse | varchar(300) | YES |  | NULL |  |
| app_state | tinyint(2) | NO |  | 1 |  |
| app_add_time | int(10) | NO |  | NULL |  |

## dberp_brand

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| brand_id | int(11) | NO | PRI | NULL |  |
| brand_name | varchar(100) | NO | MUL | NULL |  |
| brand_code | varchar(30) | YES |  | NULL |  |
| brand_sort | int(11) | NO |  | 255 |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_customer

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| customer_id | int(11) | NO | PRI | NULL |  |
| customer_category_id | int(11) | NO |  | NULL |  |
| customer_code | varchar(30) | NO | MUL | NULL |  |
| customer_name | varchar(100) | NO |  | NULL |  |
| customer_sort | int(11) | NO |  | 255 |  |
| customer_email | varchar(30) | YES |  | NULL |  |
| customer_address | varchar(255) | YES |  | NULL |  |
| customer_contacts | varchar(30) | YES |  | NULL |  |
| customer_phone | varchar(20) | YES |  | NULL |  |
| customer_telephone | varchar(20) | YES |  | NULL |  |
| customer_bank | varchar(100) | YES |  | NULL |  |
| customer_bank_account | varchar(30) | YES |  | NULL |  |
| customer_tax | varchar(30) | YES |  | NULL |  |
| customer_info | varchar(255) | YES |  | NULL |  |
| admin_id | int(11) | NO |  | NULL |  |
| region_id | int(11) | NO |  | 0 |  |
| region_values | varchar(100) | NO |  | NULL |  |

## dberp_customer_category

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| customer_category_id | int(11) | NO | PRI | NULL |  |
| customer_category_code | varchar(30) | NO | MUL | NULL |  |
| customer_category_name | varchar(100) | NO |  | NULL |  |
| customer_category_sort | int(11) | NO |  | 255 |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_ex_warehouse_order

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| ex_warehouse_order_id | int(11) | NO | PRI | NULL |  |
| warehouse_id | int(11) | NO | MUL | NULL |  |
| ex_warehouse_order_sn | varchar(50) | NO |  | NULL |  |
| ex_warehouse_order_state | tinyint(1) | NO | MUL | 6 |  |
| ex_warehouse_order_info | varchar(255) | YES |  | NULL |  |
| logistics_id | int(11) | YES |  | 0 |  |
| logistics_name | varchar(200) | YES |  | NULL |  |
| logistics_costs | decimal(10,2) | NO |  | 0.00 |  |
| logistics_sn | varchar(50) | YES |  | NULL |  |
| ex_warehouse_order_goods_amount | decimal(19,4) | NO |  | 0.0000 |  |
| ex_warehouse_order_tax | decimal(19,4) | NO |  | 0.0000 |  |
| ex_warehouse_order_amount | decimal(19,4) | NO |  | 0.0000 |  |
| ex_add_time | int(10) | NO |  | NULL |  |
| admin_id | int(11) | NO | MUL | NULL |  |

## dberp_ex_warehouse_order_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| ex_warehouse_order_goods_id | int(11) | NO | PRI | NULL |  |
| ex_warehouse_order_id | int(11) | NO | MUL | NULL |  |
| warehouse_id | int(11) | NO | MUL | NULL |  |
| warehouse_goods_ex_num | decimal(15,2) | NO |  | 0.00 |  |
| warehouse_goods_price | decimal(19,4) | NO |  | 0.0000 |  |
| warehouse_goods_tax | decimal(19,4) | NO |  | 0.0000 |  |
| warehouse_goods_amount | decimal(19,4) | NO |  | 0.0000 |  |
| goods_id | int(11) | NO | MUL | NULL |  |
| goods_name | varchar(100) | NO |  | NULL |  |
| goods_number | varchar(30) | NO |  | NULL |  |
| goods_spec | varchar(100) | YES |  | NULL |  |
| goods_unit | varchar(20) | YES |  | NULL |  |
| goods_ext_num | decimal(10,2) | YES |  | 0.00 | 退货副单位数量 |
| goods_ext_unit | varchar(20) | YES |  | NULL | 退货副单位名称 |
| goods_serial_number_str | text | YES |  | NULL |  |

## dberp_finance_payable

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| payable_id | int(11) | NO | PRI | NULL |  |
| warehouse_order_id | int(11) | NO | MUL | NULL | 入库单号 |
| p_order_id | int(11) | NO |  | NULL | 采购订单id |
| p_order_sn | varchar(50) | NO |  | NULL | 采购订单号 |
| supplier_id | int(11) | NO |  | NULL |  |
| supplier_name | varchar(100) | NO |  | NULL |  |
| payment_code | varchar(20) | NO |  | NULL | 支付方式code |
| payment_amount | decimal(19,4) | NO |  | 0.0000 | 采购支付金额 |
| finish_amount | decimal(19,4) | YES |  | 0.0000 | 采购已经支付金额 |
| purchase_invoice_id | int(11) | YES | MUL | 0 |  |
| add_time | int(10) | NO |  | NULL | 添加时间 |
| admin_id | int(11) | NO |  | NULL | 管理员id |

## dberp_finance_payable_log

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| pay_log_id | int(11) | NO | PRI | NULL |  |
| payable_id | int(11) | NO |  | NULL | 应付款账单id |
| pay_log_amount | decimal(19,4) | YES |  | 0.0000 | 付款金额 |
| pay_log_user | varchar(100) | NO | MUL | NULL | 付款人姓名 |
| pay_log_paytime | int(10) | NO |  | NULL | 付款时间 |
| pay_file | varchar(255) | YES |  | NULL |  |
| pay_log_info | varchar(255) | YES |  | NULL | 付款备注信息 |
| pay_log_addtime | int(10) | NO |  | NULL | 记录添加时间 |
| admin_id | int(11) | NO |  | NULL | 操作者id |

## dberp_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| goods_id | int(11) | NO | PRI | NULL |  |
| goods_category_id | int(11) | NO |  | NULL |  |
| brand_id | int(11) | YES |  | 0 |  |
| goods_name | varchar(100) | NO | MUL | NULL |  |
| goods_image | varchar(200) | YES |  | NULL |  |
| goods_stock | decimal(15,2) | YES |  | 0.00 |  |
| goods_spec | varchar(100) | YES |  | NULL |  |
| goods_number | varchar(30) | NO |  | NULL |  |
| unit_id | int(11) | NO |  | NULL |  |
| goods_barcode | varchar(30) | YES |  | NULL |  |
| goods_info | varchar(500) | YES |  | NULL |  |
| goods_sort | int(11) | NO |  | 255 |  |
| goods_price | decimal(19,4) | YES |  | 0.0000 |  |
| goods_serial_number_state | tinyint(1) | NO |  | 0 |  |
| admin_id | int(11) | NO |  | NULL |  |
| goods_recommend_price | decimal(19,4) | NO |  | 0.0000 |  |

## dberp_goods_category

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| goods_category_id | int(11) | NO | PRI | NULL |  |
| goods_category_top_id | int(11) | NO |  | 0 |  |
| goods_category_code | varchar(30) | NO | MUL | NULL |  |
| goods_category_name | varchar(100) | NO |  | NULL |  |
| goods_category_path | varchar(255) | YES |  | 0 |  |
| goods_category_sort | int(11) | NO |  | 255 |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_goods_custom

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| custom_id | int(11) | NO | PRI | NULL |  |
| goods_id | int(11) | NO | MUL | NULL |  |
| custom_title | varchar(50) | NO |  | NULL |  |
| custom_content | varchar(200) | NO |  | NULL |  |
| custom_key | int(2) | NO | MUL | NULL |  |

## dberp_goods_ext_unit

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| ext_unit_id | int(11) | NO | PRI | NULL |  |
| unit_id | int(11) | NO |  | NULL |  |
| unit_num | decimal(10,2) | NO |  | 0.00 |  |
| goods_id | int(11) | NO |  | NULL |  |
| unit_key | int(11) | NO |  | NULL |  |

## dberp_goods_serial_number

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| number_id | int(11) | NO | PRI | NULL |  |
| serial_number | varchar(150) | NO | MUL | NULL |  |
| serial_number_state | tinyint(1) | NO | MUL | 0 |  |
| goods_id | int(11) | NO | MUL | NULL |  |
| warehouse_id | int(11) | NO | MUL | 0 |  |
| serial_number_type | tinyint(1) | NO | MUL | NULL |  |
| outbound_in_id | int(11) | NO |  | NULL |  |
| outbound_time | int(10) | YES |  | 0 |  |
| in_time | int(10) | YES |  | 0 |  |
| return_type | tinyint(1) | YES |  | 0 |  |
| return_time | int(10) | YES |  | 0 |  |
| add_time | int(10) | NO | MUL | NULL |  |

## dberp_logistics

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| logistics_id | int(11) | NO | PRI | NULL |  |
| logistics_name | varchar(200) | NO |  | NULL |  |
| logistics_sort | int(11) | NO | MUL | 255 |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_operlog

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| log_id | int(11) | NO | PRI | NULL |  |
| log_oper_user | varchar(100) | NO | MUL | NULL |  |
| log_oper_user_group | varchar(100) | NO |  | NULL |  |
| log_time | int(10) | NO |  | NULL |  |
| log_ip | varchar(50) | NO |  | NULL |  |
| log_body | varchar(2000) | YES |  | NULL |  |

## dberp_other_warehouse_order

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| other_warehouse_order_id | int(11) | NO | PRI | NULL |  |
| warehouse_id | int(11) | NO | MUL | NULL |  |
| warehouse_order_sn | varchar(50) | NO |  | NULL |  |
| warehouse_order_state | tinyint(1) | NO |  | 3 |  |
| warehouse_order_info | varchar(255) | YES |  | NULL |  |
| logistics_id | int(11) | YES |  | 0 | 物流公司id |
| logistics_name | varchar(200) | YES |  | NULL | 物流公司名称 |
| logistics_costs | decimal(10,2) | YES |  | 0.00 | 物流费用 |
| logistics_sn | varchar(50) | YES |  | NULL | 物流单号 |
| warehouse_order_goods_amount | decimal(19,4) | NO |  | 0.0000 |  |
| warehouse_order_tax | decimal(19,4) | NO |  | 0.0000 |  |
| warehouse_order_amount | decimal(19,4) | NO |  | 0.0000 |  |
| other_add_time | int(10) | NO | MUL | NULL |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_other_warehouse_order_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| warehouse_order_goods_id | int(11) | NO | PRI | NULL |  |
| other_warehouse_order_id | int(11) | NO | MUL | NULL |  |
| warehouse_id | int(11) | NO |  | NULL |  |
| warehouse_goods_buy_num | decimal(15,2) | NO |  | 0.00 |  |
| warehouse_goods_price | decimal(19,4) | NO |  | 0.0000 |  |
| warehouse_goods_tax | decimal(19,4) | NO |  | 0.0000 |  |
| warehouse_goods_amount | decimal(19,4) | NO |  | 0.0000 |  |
| goods_id | int(11) | NO |  | NULL |  |
| goods_name | varchar(100) | NO |  | NULL |  |
| goods_number | varchar(30) | NO |  | NULL |  |
| goods_spec | varchar(100) | YES |  | NULL |  |
| goods_unit | varchar(20) | YES |  | NULL |  |
| goods_ext_num | decimal(10,2) | YES |  | 0.00 | 退货副单位数量 |
| goods_ext_unit | varchar(20) | YES |  | NULL | 退货副单位名称 |
| goods_serial_number_str | text | YES |  | NULL |  |

## dberp_plugin

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| plugin_id | int(11) | NO | PRI | NULL |  |
| plugin_name | varchar(100) | NO |  | NULL |  |
| plugin_author | varchar(100) | NO |  | NULL |  |
| plugin_author_url | varchar(200) | YES |  | NULL |  |
| plugin_info | text | NO |  | NULL |  |
| plugin_version | varchar(20) | NO |  | NULL |  |
| plugin_version_num | int(11) | NO | MUL | NULL |  |
| plugin_code | varchar(50) | NO | MUL | NULL |  |
| plugin_expired | int(10) | YES |  | 0 |  |
| plugin_state | tinyint(1) | NO | MUL | 0 |  |
| plugin_support_url | varchar(200) | YES |  | NULL |  |
| plugin_admin_path | varchar(200) | YES |  | NULL |  |
| plugin_update_time | date | NO |  | NULL |  |

## dberp_position

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| position_id | int(11) | NO | PRI | NULL |  |
| position_sn | varchar(30) | NO | MUL | NULL |  |
| warehouse_id | int(11) | NO |  | NULL |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_print_template

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| template_id | int(11) | NO | PRI | NULL |  |
| template_title | varchar(100) | NO |  | NULL |  |
| template_body | text | YES |  | NULL |  |
| template_code | varchar(50) | NO |  | NULL |  |
| template_state | tinyint(1) | NO |  | 0 |  |

## dberp_purchase_goods_price_log

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| price_log_id | int(11) | NO | PRI | NULL |  |
| goods_id | int(11) | NO | MUL | NULL |  |
| goods_price | decimal(19,0) | NO |  | NULL |  |
| p_order_id | int(11) | NO |  | NULL |  |
| log_time | int(10) | NO |  | NULL |  |

## dberp_purchase_oper_log

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| oper_log_id | int(11) | NO | PRI | NULL |  |
| p_order_id | int(11) | NO | MUL | NULL |  |
| order_state | tinyint(2) | NO |  | NULL |  |
| oper_user_id | int(11) | NO | MUL | NULL |  |
| oper_user | varchar(100) | NO |  | NULL |  |
| oper_time | int(10) | NO |  | NULL |  |

## dberp_purchase_order

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| p_order_id | int(11) | NO | PRI | NULL | 采购单id |
| p_order_sn | varchar(50) | NO | MUL | NULL | 采购单编号 |
| supplier_id | int(11) | NO |  | NULL | 供应商id |
| supplier_contacts | varchar(30) | NO |  | NULL | 供应商联系人 |
| supplier_phone | varchar(20) | YES |  | NULL | 手机号码 |
| supplier_telephone | varchar(20) | YES |  | NULL | 座机号码 |
| p_order_goods_amount | decimal(19,4) | YES |  | 0.0000 | 商品总额 |
| p_order_tax_amount | decimal(19,4) | YES |  | 0.0000 | 税金总额 |
| p_order_amount | decimal(19,4) | YES |  | 0.0000 | 订单总额 |
| p_order_info | varchar(500) | YES |  | NULL | 备注信息 |
| logistics_id | int(11) | YES |  | 0 |  |
| logistics_name | varchar(200) | YES |  | NULL |  |
| logistics_costs | decimal(10,2) | YES |  | 0.00 |  |
| logistics_sn | varchar(50) | YES |  | NULL |  |
| p_order_state | tinyint(4) | YES |  | 0 | 采购单状态，0 未审核，1 已审核，2 已入库，-1 退货，-2 退货完成 |
| payment_code | varchar(20) | NO |  | NULL |  |
| return_state | tinyint(2) | YES |  | 0 |  |
| no_review_time | int(10) | NO |  | 0 | 未审核时间 |
| review_time | int(10) | NO |  | 0 | 已审核时间 |
| wait_warehouse_time | int(10) | NO |  | 0 | 等待入库时间 |
| warehouse_time | int(10) | NO |  | 0 | 已入库时间 |
| return_time | int(10) | NO |  | 0 | 申请退货时间 |
| return_finish_time | int(10) | NO |  | 0 | 已退货时间 |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_purchase_order_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| p_goods_id | int(11) | NO | PRI | NULL | 采购单商品id |
| p_order_id | int(11) | NO |  | NULL | 采购单id |
| goods_id | int(11) | NO |  | NULL | 商品id |
| goods_name | varchar(100) | NO |  | NULL | 商品名称 |
| goods_number | varchar(30) | NO | MUL | NULL | 商品编号 |
| goods_spec | varchar(100) | YES |  | NULL | 商品规格 |
| goods_unit | varchar(20) | YES |  | NULL | 商品单位，非对应id，单位名称 |
| goods_ext_unit_id | int(11) | YES |  | 0 | 商品副单位id |
| goods_ext_num | decimal(10,2) | YES |  | 0.00 | 对应基础单位数量 |
| goods_ext_unit | varchar(20) | YES |  | NULL | 商品副单位名称 |
| p_goods_buy_num | decimal(15,2) | NO |  | 0.00 | 商品购买数量 |
| p_goods_price | decimal(19,4) | NO |  | 0.0000 | 商品购买的单价 |
| p_goods_tax | decimal(19,4) | NO |  | 0.0000 | 商品税金 |
| p_goods_amount | decimal(19,4) | NO |  | 0.0000 | 商品总金额 |
| p_goods_info | varchar(255) | YES |  | NULL | 商品备注 |
| goods_serial_number_str | text | YES |  | NULL |  |
| goods_serial_number_state | tinyint(1) | NO |  | 0 |  |

## dberp_purchase_order_goods_return

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| goods_return_id | int(11) | NO | PRI | NULL | 退货商品id |
| order_return_id | int(11) | NO |  | NULL | 退货单id |
| p_goods_id | int(11) | NO |  | NULL | 采购商品id |
| goods_name | varchar(100) | NO |  | NULL | 商品名称 |
| goods_number | varchar(50) | NO |  | NULL | 商品编号 |
| goods_spec | varchar(100) | YES |  | NULL | 商品规格 |
| goods_unit | varchar(20) | NO |  | NULL | 商品单位 |
| goods_ext_num | decimal(10,2) | YES |  | 0.00 | 退货副单位数量 |
| goods_ext_unit | varchar(20) | YES |  | NULL | 退货副单位名称 |
| p_goods_price | decimal(19,4) | NO |  | 0.0000 | 单品采购价 |
| p_goods_tax | decimal(19,4) | NO |  | 0.0000 | 税费 |
| goods_return_num | decimal(15,2) | NO |  | 0.00 | 退货数量 |
| goods_serial_number_str | text | YES |  | NULL |  |
| goods_return_amount | decimal(19,4) | NO |  | 0.0000 | 退货金额 |

## dberp_purchase_order_return

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| order_return_id | int(11) | NO | PRI | NULL | 退货单id |
| p_order_id | int(11) | NO | MUL | NULL | 采购订单id |
| p_order_sn | varchar(50) | NO |  | NULL | 采购单编号 |
| p_order_goods_return_amount | decimal(19,4) | NO |  | 0.0000 |  |
| p_order_return_amount | decimal(19,4) | NO |  | 0.0000 | 退货单金额 |
| p_order_return_info | varchar(500) | YES |  | NULL | 退货原因 |
| return_time | int(10) | NO |  | NULL | 退货单添加时间 |
| return_state | tinyint(2) | NO | MUL | -1 |  |
| return_finish_time | int(10) | YES |  | NULL |  |
| admin_id | int(11) | NO |  | NULL | 操作者id |

## dberp_purchase_warehouse_order

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| warehouse_order_id | int(11) | NO | PRI | NULL |  |
| p_order_id | int(11) | NO |  | NULL |  |
| warehouse_id | int(11) | NO |  | NULL |  |
| warehouse_order_sn | varchar(50) | NO |  | NULL |  |
| warehouse_order_state | tinyint(1) | YES |  | 2 |  |
| warehouse_order_info | varchar(255) | YES |  | NULL |  |
| admin_id | int(11) | NO |  | NULL |  |
| warehouse_order_goods_amount | decimal(19,4) | NO |  | 0.0000 |  |
| warehouse_order_tax | decimal(19,4) | YES |  | 0.0000 |  |
| logistics_costs | decimal(10,2) | YES |  | 0.00 |  |
| warehouse_order_amount | decimal(19,4) | NO |  | 0.0000 |  |
| warehouse_order_payment_code | varchar(20) | NO |  | NULL |  |

## dberp_purchase_warehouse_order_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| warehouse_order_goods_id | int(11) | NO | PRI | NULL |  |
| warehouse_order_id | int(11) | NO |  | NULL |  |
| warehouse_id | int(11) | NO | MUL | NULL |  |
| p_order_id | int(11) | NO |  | NULL |  |
| warehouse_goods_buy_num | decimal(15,2) | YES |  | 0.00 |  |
| warehouse_goods_price | decimal(19,4) | NO |  | 0.0000 |  |
| warehouse_goods_tax | decimal(19,4) | YES |  | 0.0000 |  |
| warehouse_goods_amount | decimal(19,4) | NO |  | 0.0000 |  |
| goods_id | int(11) | NO |  | NULL |  |
| goods_name | varchar(100) | NO |  | NULL |  |
| goods_number | varchar(30) | NO |  | NULL |  |
| goods_spec | varchar(100) | YES |  | NULL |  |
| goods_unit | varchar(20) | YES |  | NULL |  |
| goods_ext_unit | varchar(20) | YES |  | NULL | 扩展副单位名称 |
| goods_ext_num | decimal(10,2) | YES |  | 0.00 | 扩展副单位数量 |
| goods_serial_number_str | text | YES |  | NULL |  |

## dberp_region

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| region_id | int(11) | NO | PRI | NULL |  |
| region_name | varchar(50) | NO | MUL | NULL |  |
| region_top_id | int(11) | NO |  | 0 |  |
| region_sort | int(11) | NO |  | 255 |  |
| region_path | varchar(100) | YES |  | NULL |  |

## dberp_return_in_warehouse_order

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| in_warehouse_order_id | int(11) | NO | PRI | NULL |  |
| warehouse_id | int(11) | NO | MUL | NULL |  |
| in_warehouse_order_sn | varchar(50) | NO |  | NULL |  |
| in_warehouse_remark | varchar(300) | YES |  | NULL |  |
| sales_order_return_id | int(11) | NO | MUL | NULL |  |
| sales_order_id | int(11) | NO | MUL | NULL |  |
| in_time | int(10) | NO |  | NULL |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_sales_goods_price_log

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| price_log_id | int(11) | NO | PRI | NULL |  |
| goods_id | int(11) | NO | MUL | NULL |  |
| goods_price | decimal(19,0) | NO |  | NULL |  |
| sales_order_id | int(11) | NO |  | NULL |  |
| log_time | int(10) | NO |  | NULL |  |

## dberp_sales_oper_log

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| oper_log_id | int(11) | NO | PRI | NULL |  |
| sales_order_id | int(11) | NO | MUL | NULL |  |
| order_state | tinyint(2) | NO |  | NULL |  |
| oper_user_id | int(11) | NO |  | NULL |  |
| oper_user | varchar(100) | NO |  | NULL |  |
| oper_time | int(10) | NO |  | NULL |  |

## dberp_sales_order

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| sales_order_id | int(11) | NO | PRI | NULL | 销售订单id |
| sales_order_sn | varchar(50) | NO | MUL | NULL | 销售订单编号 |
| customer_id | int(11) | NO |  | NULL | 客户id |
| customer_contacts | varchar(30) | NO |  | NULL | 客户联系人 |
| customer_address | varchar(255) | NO |  | NULL |  |
| customer_phone | varchar(20) | YES |  | NULL | 客户手机 |
| customer_telephone | varchar(20) | YES |  | NULL | 客户电话 |
| sales_order_goods_amount | decimal(19,4) | NO |  | 0.0000 | 客户购买商品金额 |
| sales_order_tax_amount | decimal(19,4) | NO |  | 0.0000 | 商品税金 |
| sales_order_amount | decimal(19,4) | NO |  | 0.0000 | 客户购买商品总额 |
| receivables_code | varchar(20) | NO |  | NULL | 支付方式 |
| sales_order_state | tinyint(4) | NO |  | 0 | 销售状态 |
| sales_order_info | varchar(500) | YES |  | NULL | 备注说明 |
| logistics_id | int(11) | YES |  | 0 |  |
| logistics_name | varchar(200) | YES |  | NULL |  |
| logistics_costs | decimal(10,2) | YES |  | 0.00 |  |
| logistics_sn | varchar(50) | YES |  | NULL |  |
| return_state | tinyint(2) | NO |  | 0 | 退货状态 |
| no_review_time | int(10) | NO |  | 0 | 未审核时间 |
| review_time | int(10) | NO |  | 0 | 已审核时间 |
| shipping_time | int(10) | NO |  | 0 | 发货时间 |
| receiving_time | int(10) | NO |  | 0 | 收货时间 |
| return_time | int(10) | NO |  | 0 | 申请退货时间 |
| return_finish_time | int(10) | NO |  | 0 | 已退货时间 |
| admin_id | int(11) | NO |  | NULL | 操作者id |

## dberp_sales_order_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| sales_goods_id | int(11) | NO | PRI | NULL | 销售商品id |
| sales_order_id | int(11) | NO |  | NULL | 销售订单id |
| goods_id | int(11) | NO |  | NULL | 商品id |
| goods_name | varchar(100) | NO |  | NULL | 商品名称 |
| goods_number | varchar(30) | NO |  | NULL | 商品编号 |
| goods_spec | varchar(100) | YES |  | NULL | 商品规格 |
| goods_unit | varchar(20) | NO |  | NULL | 商品单位 |
| goods_ext_unit_id | int(11) | YES |  | 0 | 商品副单位id |
| goods_ext_num | decimal(10,2) | YES |  | 0.00 | 对应基础单位数量 |
| goods_ext_unit | varchar(20) | YES |  | NULL | 商品副单位名称 |
| sales_goods_sell_num | decimal(15,2) | NO |  | 0.00 | 销售商品数量 |
| sales_goods_price | decimal(19,4) | NO |  | 0.0000 |  |
| sales_goods_tax | decimal(19,4) | NO |  | 0.0000 |  |
| sales_goods_amount | decimal(19,4) | NO |  | 0.0000 |  |
| goods_serial_number_state | tinyint(1) | NO |  | 0 |  |
| sales_goods_info | varchar(255) | YES |  | NULL |  |

## dberp_sales_order_goods_return

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| goods_return_id | int(11) | NO | PRI | NULL |  |
| sales_order_return_id | int(11) | NO | MUL | NULL |  |
| sales_goods_id | int(11) | NO |  | NULL |  |
| goods_name | varchar(100) | NO |  | NULL |  |
| goods_number | varchar(50) | NO |  | NULL |  |
| goods_spec | varchar(100) | YES |  | NULL |  |
| goods_unit | varchar(20) | NO |  | NULL |  |
| goods_ext_num | decimal(10,2) | YES |  | 0.00 | 退货副单位数量 |
| goods_ext_unit | varchar(20) | YES |  | NULL | 退货副单位名称 |
| sales_goods_price | decimal(19,4) | NO |  | 0.0000 |  |
| sales_goods_tax | decimal(19,4) | NO |  | 0.0000 |  |
| goods_return_num | decimal(15,2) | NO |  | 0.00 |  |
| goods_serial_number_str | text | YES |  | NULL |  |
| goods_return_amount | decimal(19,4) | NO |  | 0.0000 |  |

## dberp_sales_order_return

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| sales_order_return_id | int(11) | NO | PRI | NULL |  |
| sales_order_id | int(11) | NO | MUL | NULL |  |
| sales_order_sn | varchar(50) | NO |  | NULL |  |
| sales_send_order_id | int(11) | NO |  | NULL |  |
| sales_send_order_sn | varchar(50) | NO |  | NULL |  |
| sales_order_goods_return_amount | decimal(19,4) | NO |  | 0.0000 |  |
| sales_order_return_amount | decimal(19,4) | NO |  | 0.0000 |  |
| sales_order_return_info | varchar(500) | YES |  | NULL |  |
| in_warehouse_state | tinyint(1) | YES |  | 0 |  |
| return_time | int(10) | NO |  | NULL |  |
| return_state | tinyint(2) | NO |  | NULL |  |
| return_finish_time | int(10) | YES |  | NULL |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_sales_send_order

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| send_order_id | int(11) | NO | PRI | NULL |  |
| send_order_sn | varchar(50) | NO |  | NULL |  |
| sales_order_id | int(11) | NO | MUL | NULL |  |
| return_state | tinyint(1) | NO |  | 0 |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_sales_send_warehouse_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| send_warehouse_goods_id | int(11) | NO | PRI | NULL |  |
| goods_id | int(11) | NO | MUL | NULL |  |
| warehouse_id | int(11) | NO |  | NULL |  |
| send_goods_stock | decimal(15,2) | NO |  | 0.00 |  |
| send_order_id | int(11) | NO |  | NULL |  |
| sales_order_id | int(11) | NO |  | NULL |  |
| goods_serial_number_str | text | YES |  | NULL |  |

## dberp_shop_order

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| shop_order_id | int(11) | NO | PRI | NULL |  |
| shop_order_sn | varchar(50) | NO |  | NULL |  |
| shop_buy_name | varchar(100) | NO |  | NULL |  |
| shop_payment_code | varchar(20) | YES | MUL | NULL |  |
| shop_payment_name | varchar(30) | YES |  | NULL |  |
| shop_payment_cost | decimal(19,4) | NO |  | 0.0000 |  |
| shop_payment_certification | varchar(500) | YES |  | NULL |  |
| shop_express_code | varchar(30) | YES |  | NULL |  |
| shop_express_name | varchar(50) | YES |  | NULL |  |
| shop_express_cost | decimal(19,4) | NO |  | 0.0000 |  |
| shop_order_other_cost | decimal(19,4) | NO |  | 0.0000 |  |
| shop_order_other_info | varchar(500) | YES |  | NULL |  |
| shop_order_state | tinyint(2) | NO |  | 10 |  |
| shop_order_discount_amount | decimal(19,4) | NO |  | 0.0000 |  |
| shop_order_discount_info | varchar(500) | YES |  | NULL |  |
| shop_order_goods_amount | decimal(19,4) | NO |  | NULL |  |
| shop_order_amount | decimal(19,4) | NO |  | NULL |  |
| shop_order_add_time | int(10) | NO |  | NULL |  |
| shop_order_pay_time | int(10) | YES |  | NULL |  |
| shop_order_express_time | int(10) | YES |  | NULL |  |
| shop_order_finish_time | int(10) | YES |  | NULL |  |
| shop_order_message | varchar(500) | YES |  | NULL |  |
| app_id | int(11) | NO |  | NULL |  |

## dberp_shop_order_delivery_address

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| delivery_address_id | int(11) | NO | PRI | NULL |  |
| delivery_name | varchar(100) | NO |  | NULL |  |
| region_info | varchar(50) | YES |  | NULL |  |
| region_address | varchar(300) | NO |  | NULL |  |
| zip_code | varchar(10) | YES |  | NULL |  |
| delivery_phone | varchar(20) | NO |  | NULL |  |
| delivery_telephone | varchar(20) | YES |  | NULL |  |
| delivery_number | varchar(30) | YES | MUL | NULL |  |
| delivery_info | varchar(500) | YES |  | NULL |  |
| shop_order_id | int(11) | NO | MUL | NULL |  |

## dberp_shop_order_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| order_goods_id | int(11) | NO | PRI | NULL |  |
| shop_order_id | int(11) | NO | MUL | NULL |  |
| distribution_state | int(1) | NO |  | 3 |  |
| warehouse_name | varchar(100) | YES |  | NULL |  |
| warehouse_id | int(11) | YES | MUL | 0 |  |
| goods_name | varchar(100) | NO |  | NULL |  |
| goods_spec | varchar(100) | YES |  | NULL |  |
| goods_sn | varchar(30) | NO |  | NULL |  |
| goods_barcode | varchar(30) | YES |  | NULL |  |
| goods_unit_name | varchar(20) | YES |  | NULL |  |
| goods_price | decimal(19,4) | NO |  | 0.0000 |  |
| goods_type | tinyint(1) | NO |  | 1 |  |
| buy_num | int(11) | NO |  | NULL |  |
| goods_amount | decimal(19,4) | NO |  | 0.0000 |  |

## dberp_stock_check

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| stock_check_id | int(11) | NO | PRI | NULL | 自增id |
| stock_check_sn | varchar(50) | NO |  | NULL | 盘点单号 |
| warehouse_id | int(11) | NO | MUL | NULL | 仓库id |
| stock_check_amount | decimal(19,4) | NO |  | 0.0000 | 盘点金额 |
| stock_check_user | varchar(100) | NO |  | NULL | 盘点人 |
| stock_check_info | varchar(255) | NO |  | NULL | 盘点备注 |
| stock_check_time | int(10) | NO |  | NULL | 盘点时间 |
| stock_check_state | tinyint(1) | NO |  | 2 | 盘点状态，1 已盘点，2 待盘点 |
| admin_id | int(11) | NO |  | NULL | 管理员id |

## dberp_stock_check_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| stock_check_goods_id | int(11) | NO | PRI | NULL |  |
| stock_check_id | int(11) | NO | MUL | NULL |  |
| stock_check_pre_goods_num | decimal(15,2) | NO |  | 0.00 |  |
| stock_check_aft_goods_num | decimal(15,2) | NO |  | 0.00 |  |
| stock_check_goods_amount | decimal(19,4) | NO |  | 0.0000 |  |
| goods_id | int(11) | NO |  | NULL |  |
| goods_name | varchar(100) | NO |  | NULL |  |
| goods_number | varchar(30) | NO |  | NULL |  |
| goods_spec | varchar(100) | NO |  | NULL |  |
| goods_unit | varchar(20) | NO |  | NULL |  |

## dberp_stock_transfer

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| transfer_id | int(11) | NO | PRI | NULL |  |
| transfer_sn | varchar(50) | NO | MUL | NULL | 调拨单号 |
| transfer_in_warehouse_id | int(11) | NO |  | NULL | 入库id |
| transfer_out_warehouse_id | int(11) | NO |  | NULL | 出库id |
| transfer_add_time | int(10) | NO | MUL | NULL | 添加时间 |
| transfer_finish_time | int(10) | YES | MUL | NULL | 完成时间 |
| transfer_info | varchar(500) | YES |  | NULL | 调拨备注 |
| transfer_state | tinyint(1) | NO |  | 0 | 调拨状态，0 待调拨，1 已调拨 |
| admin_id | int(11) | NO |  | NULL | 管理员id |

## dberp_stock_transfer_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| transfer_goods_id | int(11) | NO | PRI | NULL |  |
| transfer_id | int(11) | NO | MUL | NULL |  |
| in_warehouse_id | int(11) | NO |  | NULL |  |
| out_warehouse_id | int(11) | NO |  | NULL |  |
| transfer_goods_num | decimal(15,2) | NO |  | 0.00 |  |
| transfer_goods_state | tinyint(1) | NO |  | 0 |  |
| goods_id | int(11) | NO |  | NULL |  |
| goods_name | varchar(100) | NO |  | NULL |  |
| goods_number | varchar(30) | NO |  | NULL |  |
| goods_spec | varchar(100) | YES |  | NULL |  |
| goods_unit | varchar(20) | NO |  | NULL |  |
| goods_serial_number_str | text | YES |  | NULL |  |

## dberp_supplier

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| supplier_id | int(11) | NO | PRI | NULL |  |
| supplier_category_id | int(11) | NO |  | NULL |  |
| supplier_code | varchar(30) | NO |  | NULL |  |
| supplier_name | varchar(100) | NO | MUL | NULL |  |
| supplier_sort | int(11) | NO |  | 255 |  |
| supplier_address | varchar(255) | YES |  | NULL |  |
| supplier_contacts | varchar(30) | YES |  | NULL |  |
| supplier_phone | varchar(20) | YES |  | NULL |  |
| supplier_telephone | varchar(20) | YES |  | NULL |  |
| supplier_bank | varchar(100) | YES |  | NULL |  |
| supplier_bank_account | varchar(30) | YES |  | NULL |  |
| supplier_tax | varchar(30) | YES |  | NULL |  |
| supplier_email | varchar(30) | YES |  | NULL |  |
| supplier_info | varchar(255) | YES |  | NULL |  |
| admin_id | int(11) | NO |  | NULL |  |
| region_id | int(11) | NO |  | 0 |  |
| region_values | varchar(100) | NO |  | NULL |  |

## dberp_supplier_category

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| supplier_category_id | int(11) | NO | PRI | NULL |  |
| supplier_category_code | varchar(30) | NO | MUL | NULL |  |
| supplier_category_name | varchar(100) | NO |  | NULL |  |
| supplier_category_sort | int(11) | NO |  | 255 |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_system

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| sys_id | int(11) | NO | PRI | NULL |  |
| sys_name | varchar(30) | NO | MUL | NULL |  |
| sys_body | text | YES |  | NULL |  |
| sys_type | varchar(15) | NO |  | NULL |  |

## dberp_unit

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| unit_id | int(11) | NO | PRI | NULL |  |
| unit_name | varchar(50) | NO | MUL | NULL |  |
| unit_sort | int(11) | NO | MUL | 255 |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_warehouse

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| warehouse_id | int(11) | NO | PRI | NULL |  |
| warehouse_sn | varchar(30) | NO | MUL | NULL |  |
| warehouse_name | varchar(100) | NO |  | NULL |  |
| warehouse_contacts | varchar(50) | YES |  | NULL |  |
| warehouse_phone | varchar(30) | YES |  | NULL |  |
| warehouse_sort | int(11) | NO | MUL | 255 |  |
| admin_id | int(11) | NO |  | NULL |  |

## dberp_warehouse_goods

| 字段 | 类型 | 可空 | 键 | 默认值 | 说明 |
|---|---|---|---|---|---|
| warehouse_goods_id | int(11) | NO | PRI | NULL |  |
| warehouse_id | int(11) | NO | MUL | NULL |  |
| goods_id | int(11) | NO |  | NULL |  |
| warehouse_goods_stock | decimal(15,2) | NO |  | 0.00 |  |