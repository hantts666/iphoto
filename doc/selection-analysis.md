# 现状分析：选区操作面板（"选择与蒙版"）

> 对应版本：iPhoto 1.7.0（commit 66a3327）
> 用户评价：**功能优秀，操作迷幻。**
> 本文回答三个问题：功能有哪些？代码在哪里？操作为什么迷幻？
> 设计方案见 [selection-redesign.md](selection-redesign.md)。

---

## 1. 面板定位

"图层上面的这一块"由两部分组成，都挂在右侧 `InspectorPane.qml` 中：

| UI 区块 | 文件位置 | 说明 |
|---|---|---|
| 选区面板主体 | `src/iphoto/ui/areas/SelectionPane.qml`（全文 72 行） | 仅在 `inspectorView==="selection"` 时可见，含 3 个折叠区 + 顶部按钮 + 状态行 |
| 草稿输出条 | `src/iphoto/ui/areas/InspectorPane.qml:41-49` | 只要有选区/分区草稿就固定显示在图层列表上方：`选区→新建调整层`、`替换当前层蒙版`、`取消` |
| 对象组合条 | `src/iphoto/ui/areas/InspectorPane.qml:30-40` | 元素页勾选对象后出现：新选区 / 添加 / 减去 / 相交 |

面板的宿主状态在 `Main.qml`（即 QML 里的 `workspace`）：`selectionTool`、`selectionMode`、`brushRadius`、`tolerance`、`showMask`、`inspectorPage` 等（`Main.qml:22-48`）。后端 `editor` 是 `src/iphoto/workspace.py:36` 的 `Editor(QObject)`。

## 2. 功能清单（这块面板能做什么）

按 SelectionPane.qml 的视觉顺序：

### 2.1 顶部（常驻）
| 功能 | 触发 | 后端链路 |
|---|---|---|
| 新选区 | `SelectionPane.qml:15` | `workspace.chooseTool("rect")`（`Main.qml:54`）→ 副作用自动 `beginSelection("empty")` |
| 编辑当前层蒙版 | `SelectionPane.qml:16` | `workspace.reviewMask()`（`Main.qml:67`）→ `beginSelection("current")` + 切画笔 |
| 草稿状态说明 | `SelectionPane.qml:13` | `editor.hasSelectionDraft` / `editor.draftLabel` |

### 2.2 折叠区一：选择目标（获取选区的 4 种方式）
| 功能 | 触发 | 后端链路 |
|---|---|---|
| 在图上点目标（像素点选） | `SelectionPane.qml:20` | `chooseTool("smart")` → `startPixelSelection(false)`（`workspace.py:360`）→ EfficientSAM `segment` 请求（`controllers/pixel_selections.py`） |
| 拉框选择 | `SelectionPane.qml:21` | `chooseTool("rect")` → 画布拖拽 → `drawDraft`（`controllers/selections.py:57`） |
| AI 文字识别 | `SelectionPane.qml:23-25` | `editor.selectByDescription(text)`（`workspace.py:408` → `controllers/objects.py:28`），无场景清单先 `analyzeScene`，有则发 AI `targets` 消息 |
| 画面元素（跳转） | `SelectionPane.qml:26` | 仅切页 `workspace.inspectorPage=2`，实际操作发生在 ScenePane/画布 |
| 直接识别此目标（AI 对话兜底） | `SelectionPane.qml:28` | `editor.sendMessage(text,"selection")`（`controllers/conversation.py:234-248` 将 AI 结果转成选区） |
| 取消 AI 请求 | `SelectionPane.qml:29` | `editor.ai.cancel()` |

### 2.3 折叠区二：检查与修边
| 功能 | 触发 | 后端链路 |
|---|---|---|
| 蒙版查看方式（绿覆盖/黑白/调色预览） | `SelectionPane.qml:36` | `setMaskView`（`selections.py:109`）+ `workspace.showMask` |
| 按原图细化边缘（alpha matting） | `SelectionPane.qml:41-44` | `editor.refineMatte(radius)`（`workspace.py:348` → `controllers/matting.py:6`）→ worker `matte` → closed-form matting |
| 边缘色彩保护 | `SelectionPane.qml:45-48` | `setEdgeProtection`（`selections.py:326`），写入 mask 协议字段 |
| 质量反馈文案 | `SelectionPane.qml:51` | `editor.selectionQuality` ← `_quality_text`（`selections.py:12`，`segmentation/classical.py:assess` 覆盖率+警告） |

### 2.4 折叠区三：手动修正与更多工具
| 功能 | 触发 | 后端链路 |
|---|---|---|
| 补点/排除点 | `SelectionPane.qml:55` | `chooseTool("smart")` + `startPixelSelection(true)`（以当前草稿为 hint） |
| 画笔 B | `SelectionPane.qml:56` | `chooseTool("brush")` + `selectionMode="add"` |
| 全选/清空/反选 | `SelectionPane.qml:60-62` | `draftAction`（`selections.py:79`） |
| 重新识别轮廓 | `SelectionPane.qml:64` | `editor.pixelRefine()`（`workspace.py:376`，当前草稿作 hint 再跑一次 SAM） |
| 选择主体 / 配置主体模型 | `SelectionPane.qml:65` | `refineSelection("u2net")`（`selections.py:119`）或打开能力对话框 |
| 内羽化 | `SelectionPane.qml:68` | `setDraftFeather`（`selections.py:95`） |

### 2.5 输出（在 InspectorPane 底部条，不在面板内）
| 功能 | 触发 | 后端链路 |
|---|---|---|
| 选区 → 新建调整层 | `InspectorPane.qml:43` | `selectionToLayer`（`selections.py:152`） |
| 替换当前层蒙版 | `InspectorPane.qml:45` | `acceptSelection`（`selections.py:289`） |
| 取消 | `InspectorPane.qml:46` | `discardSelection`（`selections.py:309`） |

### 2.6 面板外但同属选区体系的功能（分散点）
- **顶部工具栏**（`Main.qml:201-224`）：工具名、选区模式（新选区/＋添加/－减去）、笔刷大小滑杆、魔棒容差、smart 工具的"新目标/退一点/点数"、载入蒙版、显示蒙版。
- **ToolRail**（`ToolRail.qml:11-22`）：10 个工具按钮 + AI 按钮（跳选区页）。
- **画布交互**（`CanvasViewport.qml:60-100`）：拖拽成形状、点击派发（smart→`pixelPoint`、object→`clickObject`、wand→`wandSelection`、其余→`drawDraft`）；Alt 临时减选、Shift 临时加选（`CanvasViewport.qml:74`）。
- **快捷键**（`Main.qml:158-184`）：V/H/Z/M/L/B/O/S/W 切工具、X 切加减、`[`/`]` 调笔刷、Q 蒙版、Ctrl+A/D/Shift+I。
- **菜单"选择"**（`Main.qml:135-141`）：新建选区、载入当前层蒙版、全选、反选、取消选区。
- **RegionPane**（`RegionPane.qml:25-27`）：分区草稿内也能"优化当前区域边缘"（grabcut）、"细化透明边缘"（matte，固定 radius=8）。
- **LayersPane**（`LayersPane.qml:31-35`）：点蒙版缩略图 → `selectLayer` + `reviewMask`。
- **AdjustmentPane**（`AdjustmentPane.qml:47`）：组的"编辑组蒙版" → `reviewMask`。
- **CanvasPane**（`CanvasPane.qml:22-29`）：草稿状态条、取消选区/方案按钮。

## 3. 后端代码分布

```
workspace.py:36 Editor(QObject)          ← QML 的 editor，选区 API 的门面
 ├─ controllers/selections.py   草稿生命周期/绘制/修边参数/输出（331 行，函数式 mixin）
 ├─ controllers/pixel_selections.py  EfficientSAM 点选编排
 ├─ controllers/matting.py      alpha matting 事务（start/cancel/complete）
 ├─ controllers/objects.py      场景清单、文字选目标、对象集合运算
 ├─ controllers/worker_bridge.py  _request/_read 分发 segment/matte/selection/render
 ├─ controllers/conversation.py:234-248  AI 对话结果 → 选区
worker.py:126-145               子进程执行 selection/segment/matte
document.py                     mask 协议（empty_mask/validate_mask/raster_mask/selection_draft 持久化）
segmentation/                   classical(grabcut/wand/subject/assess)、efficient_sam、grounding
matting/                        trimap/solver/service
plugins.py:88-129               能力检测与选区后端注册表
```

关键机制：
- **草稿事务**：所有修改经 `_set_candidate`（`selections.py:21`）→ `validate_mask` → `_record_draft`（独立 24 步历史栈）→ `_generation++` → `_schedule_render`（把草稿临时塞进图层树出预览，不改真图层）。
- **undo 分裂**：`editor.undo/redo` 在 `_candidate` 非空时只操作 `_draft_history`，否则操作文档历史（`workspace.py:316/334`）。
- **并行双 API**：草稿系（`drawDraft/draftAction/setDraftFeather`）与直接改层系（`drawSelection/selectionAction/setFeather`，`selections.py:245-286`）是两套等价实现，UI 几乎只用草稿系。
- **死代码**：`setAutoRefine`（`selections.py:8`）写入的 `_auto_refine` 全仓无读取。

## 4. 问题诊断：为什么"操作迷幻"

### P1. 四种"细化/修边"并存，语义无法区分
`按原图细化边缘`（matte）、`重新识别轮廓`（pixelRefine/SAM）、`选择主体`（u2net）、菜单与 RegionPane 里的 `优化边缘`（grabcut）——四个动词近似的按钮走四条完全不同的算法，界面上没有任何解释差异的信息。用户只能靠试。

### P2. 输出语义模糊且位置割裂
"选区→新建调整层"和"替换当前层蒙版"是本质不同的破坏性等级（新建 vs 覆盖），却并排放在底部条（`InspectorPane.qml:43-45`），面板主体内完全看不到；按钮文案不说明后果。"取消"同时服务选区草稿和分区草稿两种状态。

### P3. 隐式副作用驱动的模式切换
- `chooseTool`（`Main.qml:54-62`）：点任意非导航工具会**自动创建空选区草稿**、自动切 inspector 页、自动开蒙版显示、自动关对比——用户点一下 M，界面四处联动变化，无提示。
- `Connections.onChanged`（`Main.qml:246-257`）：草稿出现/消失时又反向切页、重置工具、关 showMask。UI 状态由事件副作用推动，用户难以建立"我现在处于什么模式"的心智模型。
- 折叠区展开状态绑定 `hasSelectionDraft`/`selectionAiOpen`（`SelectionPane.qml:18/33`），面板会自动开合。

### P4. 同一功能多入口，行为细节不一致
"进入蒙版编辑"至少 5 个入口（ToolRail、SelectionPane 顶部、菜单、工具栏"载入蒙版"、LayersPane 缩略图、AdjustmentPane 组蒙版）；修边在 SelectionPane 和 RegionPane 各有一套（RegionPane 的 matte 固定 radius=8，SelectionPane 可调）。入口越多，用户越不确定它们是否等价。

### P5. 工具选项与面板空间分离
选区模式（replace/add/subtract）、笔刷大小、魔棒容差在**顶部工具栏**（`Main.qml:204-212`），而选区功能面板在**右侧**，画布操作在**中间**；同时 Alt/Shift 在画布上临时覆盖模式、X 键全局切换模式。同一概念三处控制、三种交互，视线要横跨整个窗口。

### P6. 按钮可用性逻辑复杂，灰了不解释
enabled 是 `hasImage && !busy && hasSelectionDraft && matteAvailable && subjectAvailable …` 的各种组合；禁用原因（没图？在算？没模型？没草稿？）不反馈给用户。"选择主体"一个按钮身兼"执行"与"去配置模型"两种行为（`SelectionPane.qml:65`）。

### P7. 两个"取消"、两种忙状态
`取消 AI 请求`（`editor.ai.busy`）与 `取消当前计算`（`pixelBusy || matteBusy`）分列两处（`SelectionPane.qml:29/32`），用户需要理解后台有三条互相独立的任务管线才能选对。

### P8. AI 文字选择的三条路径令人困惑
`AI 识别`（selectByDescription，走本地场景清单/AI targets）、`画面元素`（跳页勾选对象）、`清单没有？直接识别此目标`（sendMessage 走对话）——三个按钮是同一意图（"用文字选东西"）在不同数据条件下的分支，却被平铺成并列选项，条件（有无 catalog）对用户不可见。

### P9. 面板内跳转打断流程
`画面元素` 按钮只是 `inspectorPage=2` 的页面跳转（`SelectionPane.qml:26`），用户从"选区"心智被扔到"元素"页，完成后如何回到选区流程无引导（实际靠 `combineObjects` 底部条，但那在另一个视图状态里）。

## 5. 测试基线（重构的回归保障）

- `tests/test_pixel_selection.py`：SAM 契约、提示点、候选评估、缺模型不降级。
- `tests/test_pixel_workflow.py`：S 键进入 smart、点击/Alt 点击、退点、取消、结果竞态。
- `tests/test_matting.py`：羽化/trimap/closed-form 精度、失败取消过期保护、真 worker 全链（草稿→refineMatte→undo/redo→存盘→accept→导出）。
- `tests/test_redesign_ui.py`：工具拖拽成草稿、图层不被改、Ctrl+D、grabcut、selectionToLayerButton、分区草稿、模态内快捷键屏蔽。
- `tests/test_redesign.py`、`test_v14.py`、`test_layers.py`：GrabCut 保洞、wand、草稿事务独立撤销、对象集合运算、蒙版外像素保护。

**结论**：后端管线（segmentation/matting/worker/document）质量高、测试厚，是"功能优秀"的来源；迷幻集中在 **QML 层的交互组织、状态副作用与文案**，以及 **Editor 门面上语义重叠的 API**。重构应主要动 UI 组织与 API 门面，不动算法管线。
