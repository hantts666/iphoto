# iPhoto 1.4 · AI 连接与千问平台适配

更新：2026-09-23。

## 使用步骤

1. 双击根目录 `start-iphoto.cmd`，确认窗口标题是 **iPhoto 1.4**。已经打开的旧版窗口需要重新启动，运行中的旧进程不会自动加载新代码。
2. 点击右上角 **AI 设置**。没有配置 Key 时，输入区的 **连接 AI** 也会打开此窗口。
3. 服务商选择 **千问 AI 平台 · Token Plan 个人版**。地址与默认看图模型 `qwen3.8-flash` 自动填好。
4. 从[个人套餐页面](https://platform.qianwenai.com/home/analytics/token-plan/individual)获取自己的套餐专属 Key（`sk-sp-` 开头），粘贴到 API Key 输入框。
5. 点击 **测试连接**：发送一张应用生成的色块图，验证鉴权、模型图片输入与 JSON 参数返回。测试会消耗少量套餐用量，不发送用户照片。
6. 成功后点击 **保存并使用**，回到主界面输入要求，再点击 **AI 修图**。

模型支持的项目权限、套餐授权、余额与网络是否可用，以本人账号和实际测试结果为准。模型名称可以手动修改，但需要支持图片理解与 JSON 输出。`qwen3.8-max`、`qwen3.7-plus` 也可用于效果比较；纯文本模型和图片生成模型不适合当前参数规划接口。

## 服务商与地址

| 选项 | Base URL | Key 类型 |
|---|---|---|
| 千问 Token Plan 个人版 | `https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1` | 套餐专属 `sk-sp-` |
| 千问按量计费 | `https://maas.qianwenaiapi.com/compatible-mode/v1` | 通用按量 Key |
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 对应地域的百炼 Key |
| OpenAI | `https://api.openai.com/v1` | OpenAI API Key |
| 自定义 | 用户填写 | 对应接口自己的 Key |

Token Plan 选项也接受平台部分官方客户端文档列出的 `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。应用不自动在两个域名间切换；Key 绑定到用户选择的准确地址。Base URL 可以填到 `/v1` 或粘贴完整 `/chat/completions`，后者会被规范化。

Token Plan 与按量 Key 不可混用。套餐选项拒绝普通 Key、控制台网页地址和按量 API 地址；按量选项也会拒绝 `sk-sp-`。失败、限额与超时均不会自动转按量通道。

## 平台使用范围

[千问其他 AI 工具说明](https://platform.qianwenai.com/docs/developer-guides/clients-and-developer-tools/other-tools)目前明确对 Token Plan 用于自定义应用程序直接调用设有限制；[概述](https://platform.qianwenai.com/docs/token-plan/overview)与部分客户端说明又描述了协议兼容接入。协议适配不等于获得套餐使用授权，iPhoto 未验证用户账号的实际许可。具体使用范围以服务商对用户套餐的授权为准；需要通用应用接口时可自行选择独立的按量选项。应用没有伪装成其他客户端，也没有后台批量或定时调用。

[个人版说明](https://platform.qianwenai.com/docs/token-plan/personal/token-plan-personal-overview)还包含输入及生成内容用于服务改进与模型优化的约定。发送私人照片前请了解本人套餐的数据规则；应用界面说明了会发送的内容。

## Key、照片与失败处理

- 默认使用 Windows Credential Manager 的 Generic Credential，按服务商和完整 Base URL 的哈希隔离。模型可更换，地址变化不会复用旧 Key。
- 设置文件只含服务商、地址、模型与模式；Key 不写入设置 JSON、照片编辑记录、日志或命令行。
- 取消“记住 Key”后仅在当前进程内保存；关闭应用即失效，并删除该地址已有的持久凭据。设置窗口再次打开时不回显已保存 Key；留空表示继续使用已保存的值。
- 云端收到最长边 1280px、去掉 EXIF 的 JPEG 缩略图、用户要求、当前参数、手动锁定字段、当前图层选区及最近最多 8 条对话。源文件名、路径和原尺寸图片不会作为请求发送。
- 网络采用 Qt 异步请求；可取消，总超时 60 秒，不自动重试，不跟随重定向，不忽略 HTTPS 证书错误。HTTP 错误使用本地解释，不直接展示可能回显密钥的服务端错误正文。
- 只接受完整的十二字段有限数值参数。越界、额外字段、缺字段、截断、拒绝或非法 JSON 均不应用；手动锁定值在本机再次保护。
- 明确不支持的内容生成/修复要求返回说明并保持参数；云端失败不会静默运行本地关键词规则。

## v1.3 工作流

下方 AI 修图助手选择“当前图层”“只给建议”或“自动分区”。右侧“选区”面板有独立输入框，只描述要选中的区域；修图要求不会混进选区请求。建议保存到对话，点击“应用建议”才改变照片。

文字选区首先建立可复用的画面元素清单，然后由模型返回需要选择和排除的对象 id。本地组合已有轮廓，产生独立草稿，不直接修改图层。后续文字要求只发送对象名称、类别、id 与要求，不再次上传照片。右侧“元素”还支持按类别勾选和组合；O 键在画布悬停预览、点选。清单缺少目标时可用“直接识别此目标”重新描绘。GrabCut 默认由用户手动触发；已有轮廓保留内部，只重判较窄的边界带。

“自动分区”让 AI 判断最多四个区域，每区给出名称、理由、蒙版和参数；先预览整张照片与各区蒙版，勾选需要的区域，确认后一次性建立独立调整层。原有图层不被改写；整组可以一步撤销。

调色/建议模式额外发送最长边 512px 的黑白蒙版和已有图层信息，并明确区分原图与蒙版。场景分析、直接轮廓和分区模式只发送一张原图；对象 id 选择模式不带图片。场景/轮廓/分区响应上限 8192 tokens，调色/建议/对象选择为 4096。场景分析超时上限 90 秒，其余 60 秒，可取消；不自动重试或转按量接口。项目恢复本身不发起请求。

## 实现

`ai_settings.py` 负责配置与凭据；`ai.py` 管理网络；`ai_protocol.py` 构造请求，`ai_tasks.py` 与 `scene.py` 校验结果；`controllers/conversation.py` 和 `controllers/objects.py` 组织工作流程；`AISettingsDialog.qml` 提供设置界面。详见[仓库说明](repository-guide.md)。

千问系列使用 OpenAI Chat Completions 的图文消息、`enable_thinking=false` 和 `response_format=json_object`，在本地执行严格字段校验。OpenAI 选项使用严格 `json_schema`。自定义选项要求兼容 Chat Completions、图片输入与 JSON Object。

## 验证

自动化使用本机 HTTP 模拟服务与无效的合成测试 Key，不消耗用户云端用量。覆盖连接探测、两种窗口尺寸下真实控件操作、保存与重启读取、密码遮罩、参数锁定、云端结果进入撤销/重做/导出、原图保护、各类 HTTP 错误、重定向拒绝、取消、超时、缩略图清除元数据、Token Plan Key/地址隔离。

真实 Windows 凭据 API 已在正常登录会话中完成随机测试项的写入、读取和删除。Codex 测试沙箱可能返回 Windows 1312（无登录凭据会话），此项在沙箱下跳过，单独在正常会话运行。

本轮已按用户授权读取本机保存的千问 Token Plan 凭据，以 qwen3.8-flash 对内置湖景进行 10 次真实请求。记录包含旧协议失败、一次分析超时、实际元素清单和组合选区。复用清单后的两次选择约 2.3 秒与 2.2 秒；这不是跨照片成功率统计。未对 OpenAI 做真实调用。详见[v1.4 验收](../planning/iphoto-v1.4/delivery.md)。

## 官方接口依据

- [千问 Qwen Code 配置：Token Plan 专属地址和模型](https://platform.qianwenai.com/docs/developer-guides/clients-and-developer-tools/qwen-code)
- [千问其他 AI 工具：专属地址及使用范围](https://platform.qianwenai.com/docs/developer-guides/clients-and-developer-tools/other-tools)
- [千问 API Key](https://platform.qianwenai.com/docs/api-reference/preparation/api-key)
- [千问结构化输出](https://platform.qianwenai.com/docs/developer-guides/text-generation/structured-output)
- [千问模型与思考模式](https://platform.qianwenai.com/docs/developer-guides/getting-started/latest-model)
- [千问错误码](https://platform.qianwenai.com/docs/api-reference/preparation/error-messages)
- [OpenAI 图片输入](https://developers.openai.com/api/docs/guides/images-vision)
- [OpenAI 结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)
