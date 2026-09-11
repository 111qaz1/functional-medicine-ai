# Joolun 开方页嵌入契约

## 启用门槛

生产环境启用前，双方必须确认以下值：

- 甲方稳定医生 ID（不能使用姓名或手机号替代）
- 甲方稳定患者 ID
- 在处方提交前已经存在的就诊 ID 或开方草稿 ID
- 甲方开方页精确 Origin
- 甲方后端可调用功能医学服务的服务端换票能力
- 经双方审核的 `sku_id -> goodsId + specId` 清单及每日剂量换算规则

任何一项未确认时，双端 Feature Flag 必须保持关闭。甲方页面仍可手工开方。

## 功能医学服务配置

以下配置默认关闭，不应把共享密钥写入前端构建变量：

```text
FM_JOOLUN_EMBED_ENABLED=0
FM_JOOLUN_EMBED_SHARED_SECRET=<server-only-secret>
FM_JOOLUN_EMBED_BASE_URL=https://functional-medicine.example
FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS=https://doctor.example
FM_JOOLUN_SKU_MAPPING_PATH=/run/secrets/joolun_sku_mapping.json
```

甲方 Vue 构建只需要非敏感配置：

```text
VITE_FM_EMBED_ENABLED=false
VITE_FM_EMBED_ORIGIN=https://functional-medicine.example
```

## 甲方后端职责

Vue 页面调用甲方同源接口：

```http
POST /admin/api/doctor/integrations/functional-medicine/embed-session
Content-Type: application/json

{
  "externalPatientId": "patient-stable-id",
  "externalEncounterId": "encounter-or-prescription-draft-id",
  "patientName": "合成患者"
}
```

甲方后端必须从当前登录会话取得医生 ID/姓名，不能信任浏览器提交的医生身份。随后由甲方服务器调用：

```http
POST /api/v2/integrations/joolun/embed-sessions
```

请求字段：

- `issuer`
- `external_doctor_id`
- `doctor_name`
- `external_patient_id`
- `external_encounter_id`
- `patient_name`
- `parent_origin`
- `timestamp`（Unix 秒）
- `nonce`（至少 16 字符，每次请求唯一）
- `signature`（HMAC-SHA256 十六进制）

签名原文使用 UTF-8，并按以下顺序用换行符连接；所有字段先去除首尾空白，`issuer` 转小写，`parent_origin` 去除末尾 `/`：

```text
issuer
external_doctor_id
doctor_name
external_patient_id
external_encounter_id
patient_name
parent_origin
timestamp
nonce
```

响应中的 `embed_url` 原样返回 Vue 页面。共享密钥和功能医学 Bearer session 不得返回浏览器、写入 URL、日志或前端存储。

## 身份与幂等规则

- 签名时间窗口为 5 分钟。
- nonce 只能使用一次。
- embed ticket 60 秒有效且只能兑换一次。
- 兑换后生成 8 小时 HttpOnly 嵌入会话。
- 病例唯一键为 `(issuer, external_doctor_id, external_encounter_id)`。
- 同一就诊的患者 ID 或医生 ID 发生冲突时拒绝换票，不修改已有映射。

## 商品映射

映射文件只保存双方审核过的商品/规格 ID；剂量来自医生最终批准的方案，不在 SKU 映射中静态配置：

```json
{
  "version": 2,
  "mappings": [
    {
      "sku_id": "approved-internal-sku",
      "status": "mapped",
      "goods_id": "partner-goods-id",
      "spec_id": "partner-spec-id",
      "product_type": 0,
      "enabled": true
    }
  ]
}
```

暂缺商品使用 `status: "unmapped"` 和明确 `reason` 保留；甲方父页面收到推荐后必须再次向甲方后端核验规格、库存和机构价格。核验失败、缺货、停用或未映射的商品不加入。

当前 `sku_super_anti_inflammatory` 使用 `partner_product_missing` 暂缓回填；`sku_zinc_complex` 固定映射至 `2091816225546137602 / 2091817634534486017`，不得与肌肽锌或另一组同名锌商品互换。

剂量回填使用批准方案中的 `dosage_option_id`、`dosage_text` 和 `dosage_regimen`。可唯一换算为每日正整数时返回 `dose_status: "resolved"`；否则返回 `dose_status: "pending"` 和空剂量字段，由医生确认后才能发送处方。

## 父子页面事件

子页面只向换票时绑定的 `parent_origin` 发送：

- `fm.embed.resize`，版本 `1`，负载 `{ height }`
- `fm.recommendations.approved`，版本 `2`，负载包含就诊 ID、病例 ID、草案 ID、修订号、剂量状态、映射项和未映射 SKU

父页面校验 `event.source`、`event.origin`、事件版本、就诊 ID 和完整字段结构。幂等键为 `draft_id + revision`。已有购物车商品不覆盖，仅增加 AI 推荐来源标记。
