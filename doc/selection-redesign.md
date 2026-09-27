# 重构设计：选区功能公共化 + 操作重组

> 前置阅读：[selection-analysis.md](selection-analysis.md)（问题编号 P1-P9 在本文引用）
> 设计原则：**后端管线不动，API 门面收敛，UI 按用户任务重组，状态显式化。**

---

## 一、目标

1. **抽出公共代码**：把散布在 `Main.qml`（workspace 全局属性）、`SelectionPane`、`RegionPane`、`InspectorPane`、`CanvasViewport`、菜单中的选区能力收敛为单一事实源——Python 端 `SelectionController` + QML 端共享组件。
2. **重组操作**：按用户真实任务流"①做出选区 → ②修好边缘 → ③明确输出"组织界面，消除 P1-P9 的迷幻点。
3. **可回归**：现有测试（见分析文档第 5 节）作为行为基线，公共 API 保持槽名兼容或同步改测试。

## 二、公共代码抽取设计

### 2.1 Python 端：`SelectionController`（新 QObject，单一事实源）

现状：选区状态三分——Editor 内部字段（`_candidate/_draft_history/_mask_view/...`）、QML `workspace` 属性（`selectionTool/selectionMode/brushRadius/tolerance/showMask`）、各面板局部控件值（matteRadius SpinBox、edgeProtection 读 editor）。

方案：新建 `src/iphoto/controllers/selection_controller.py`，注册为 QML context property `selection`（Editor 持有并转发，保持 `editor.*` 旧槽为薄委托，逐步废弃）。收敛内容：

```
SelectionController(QObject)
 ├─ 工具状态（从 Main.qml 迁入，解决 P5）
 │    tool: "inspect|smart|object|hand|zoom|rect|ellipse|polygon|brush|wand"
 │    mode: "replace|add|subtract"        # Alt/Shift 临时覆盖仍在画布层，但结果回写此处
 │    brushRadius / wandTolerance
 │    chooseTool(tool)                    # 唯一入口，副作用集中且显式（见 3.4）
 ├─ 草稿生命周期
 │    begin(source="empty|current") / discard() / undo() / redo()
 │    apply(mode="new_layer|replace_mask") # 统一 selectionToLayer+acceptSelection（解决 P2）
 │    draft: {label, quality, coverage, feather, edgeProtection, maskView}
 ├─ 获取目标（4 种方式统一为一组语义化槽）
 │    selectByText(text)                  # 内部自动分支 catalog/AI targets/对话（解决 P8）
 │    pixelPoint(x,y,exclude) / undoPixelPoint() / resetPixel()
 │    drawShape(kind, mode, points, radius)
 │    combineObjects(mode)
 │    draftAction("all|clear|invert")
 ├─ 边缘处理（统一门面，解决 P1）
 │    refine(method, params)              # method ∈ auto|matte|sam|subject|grabcut
 │    refineMethods: [{id,name,available,description}]  # 由 plugins.capabilities 生成
 │    cancelTask()                        # 统一取消（解决 P7）
 │    task: {kind:"none|pixel|matte|ai", progressText}
 └─ 视图
      maskView: "overlay|grayscale|adjustment"
      showMask（从 Main.qml 迁入）
```

要点：
- **删除双 API**：`drawSelection/selectionAction/setFeather`（直接改层系，`selections.py:245-286`）确认 UI 无引用后删除，只保留草稿系；"直接编辑层蒙版"统一为 `begin("current")` → 修改 → `apply("replace_mask")`。删除死代码 `setAutoRefine/_auto_refine`。
- **任务状态机**：`task.kind` 单值枚举（none/pixel/matte/ai），QML 只读它渲染唯一的"取消"按钮与状态行，替代 `pixelBusy/matteBusy/ai.busy` 三态裸露（解决 P7）。
- **refine 自动路由**：`refine("auto")` 按可用性选最佳方法（matte 需 `_candidate` 有过渡带、subject 需 u2net、否则 grabcut），`refineMethods` 供高级下拉展示每项的一句话解释（解决 P1）。
- RegionPane 复用同一门面：`selection.refine("grabcut"/"matte")` 作用于当前选中分区（controller 内部感知 `_region_candidate`，现有 `refineSelection` 已这样做，`selections.py:129-134`）。
- `selections.py` 等 mixin 保留为 controller 的实现细节，Editor 上的旧槽标记 deprecated 但保留一个版本期，测试逐个迁移。

### 2.2 QML 端：共享组件（解决 P4、P5）

新增 `src/iphoto/ui/components/selection/`：

| 组件 | 内容 | 复用方 |
|---|---|---|
| `DraftOutputBar.qml` | 草稿输出条：apply(new_layer)/apply(replace_mask)/discard + 后果说明文案 | InspectorPane 底部（现 `InspectorPane.qml:41-49`）、CanvasPane 状态条 |
| `RefineControls.qml` | 统一修边组："智能细化"主按钮 + 方法下拉（名称+可用性+一句话说明）+ 参数（matte radius、edge protection、feather） | SelectionPane、RegionPane（替换 `RegionPane.qml:25-27` 固定 radius=8 的写法） |
| `MaskViewControls.qml` | 查看方式三态切换 | SelectionPane、RegionPane |
| `ToolOptionsBar.qml` | 模式(replace/add/subtract)、笔刷大小、魔棒容差，绑定 `selection.*` | 画布顶部浮动条（新位置）或保留顶部工具栏，二选一但只此一份 |
| `TaskStatusBar.qml` | 当前任务文案 + 唯一取消按钮 | SelectionPane、CanvasPane |

`Main.qml` 中的 `selectionTool/selectionMode/brushRadius/tolerance/showMask` 属性删除，全部改绑 `selection.*`；快捷键（`Main.qml:158-184`）保留但调用 `selection.chooseTool/...`。

## 三、操作重组设计（新 SelectionPane）

### 3.1 信息架构：三步任务流

```
┌ 选择与蒙版 ─────────────────────────┐
│ [草稿状态条]  空选区 · 覆盖 0% · 未修改 │   ← 常驻，无草稿时显示引导文案
│                                     │
│ ① 做出选区                          │   ← 常驻展开
│   [🖱 点目标] [▭ 拉框] [✍ 描述选择]   │
│   描述输入框 + [识别]（单按钮，见 3.3）  │
│   更多方式 ▾（套索/椭圆/魔棒/画笔/元素/主体）│
│                                     │
│ ② 修边与检查            （有草稿才启用）│
│   查看方式 [绿覆盖|黑白|调色]          │
│   [智能细化 ▾方法]  边缘范围 [8]px     │
│   边缘色彩保护 ────○── 30%            │
│   内羽化 ──○──── 0.5%                │
│   质量：覆盖 42% · 边缘有碎片（assess） │
│                                     │
│ ③ 输出                  （有草稿才显示）│
│   [新建调整层]（主按钮，附一句后果说明）  │
│   [替换当前层蒙版]（次按钮，警示色）     │
│   [取消草稿]                         │
└─────────────────────────────────────┘
```

- 三步即三个区块，**不再用自动开合的折叠区**（解决 P3 的面板自动开合）：②③ 用 enabled/visible 跟随草稿状态，但布局位置固定，用户能建立空间记忆。
- ③ 输出条同时保留在 InspectorPane 底部（`DraftOutputBar` 组件复用），两处同源同文案（解决 P2 的位置割裂）。

### 3.2 "做出选区"区：方式平权、入口唯一

- 三个一等公民按钮：**点目标**（smart）、**拉框**（rect）、**描述选择**（文本框+识别）；其余方式（套索 L、椭圆、魔棒 W、画笔 B、画面元素 O、选择主体）收进"更多方式"下拉，每项带快捷键提示。
- "画面元素"不再裸跳页（解决 P9）：点击后跳转并在选区页保留一条"元素勾选完成后，用底部『新选区/添加/减去』返回"的引导（对象组合条已在 `InspectorPane.qml:30-40`，为其补一句返回路径说明）。
- 所有入口（ToolRail、菜单、工具栏、面板）统一调 `selection.chooseTool`，副作用一处实现（解决 P4）。

### 3.3 文字选择单入口（解决 P8）

删除并列的"AI 识别 / 清单没有？直接识别此目标"两个按钮，只留一个 **[识别]**。`selectByText` 内部路由：

```
有场景清单且命中 → 本地对象掩码（快，无网络）
有清单未命中 / 无清单 → AI targets 请求
AI 失败或用户再点 → 追加提示"转为对话识别？"（显式升级，而非隐式第三个按钮）
```

路由结果通过 `TaskStatusBar` 文案可见（"正在用画面元素匹配…" / "正在请求 AI…"），用户始终知道走的哪条路。

### 3.4 chooseTool 副作用显式化（解决 P3）

保留"选工具即开草稿"的便利（这是产品优点），但让状态变化可感知：
- 草稿创建时 `DraftOutputBar` 滑入 + toast 一次性说明"已创建选区草稿，原图层不受影响，直到你点击输出"。
- 删除 `Main.qml:246-257` `Connections.onChanged` 里的反向副作用切页逻辑，改为 controller 发出显式信号（如 `selection.draftBegan/draftEnded`），QML 只订阅这两个信号做视图切换，数据流单向。
- 折叠/跳页联动全部由 `selection` 状态驱动（QML 绑定），不再有命令式赋值。

### 3.5 修边区：一个主概念 + 高级展开（解决 P1）

- 主按钮 **[智能细化]** = `refine("auto")`；旁边 ▾ 展开方法列表，来自 `selection.refineMethods`，每项显示：名称（细化透明边缘/重识轮廓/选择主体/经典优化）、可用性、一句话适用场景。不可用项置灰并注明原因（如"需配置 u2net 模型 →"附打开能力对话框的链接，解决 P6 的一钮双义）。
- "补点/排除点"归入①区 smart 工具的上下文（选 smart 工具时顶部工具栏已有"新目标/退一点"，面板不再重复）。
- 参数（边缘范围、色彩保护、羽化）留在修边区，全部绑定 `selection.draft.*`，RegionPane 复用同组件。

### 3.6 输出区：后果可见（解决 P2）

- 主按钮 **[新建调整层]**：说明文案"创建一个带此蒙版的新调整层，原图层不变"。
- 次按钮 **[替换当前层蒙版]**：警示色 + "覆盖当前层的现有蒙版，可 Ctrl+Z 撤销"。
- 分区草稿时同一组件渲染为 [确认并创建分区图层]/[取消]（现有语义不变）。
- 空选区点输出 → 现有校验（`selections.py:157`）+ toast 说明原因。

### 3.7 禁用态与帮助（解决 P6）

- 统一规则：`tool==="none" || task.kind!=="none"` 时禁用操作类按钮；每个禁用按钮通过 `Action.hint`（组件已支持，见 `Main.qml:220` 用法）给出一句话原因，鼠标悬停可见。
- 面板底部常驻一行快捷键速查（M/L/B/S/W/O、X、[/]、Q、Ctrl+A/D/Shift+I），点击可展开完整表。

## 四、实施计划（分 4 步，每步可独立回归）

| 步骤 | 内容 | 涉及文件 | 回归 |
|---|---|---|---|
| S1 | Python 端 `SelectionController` 落地：迁移工具状态与任务状态机，Editor 旧槽薄委托；删双 API 与 `_auto_refine` | 新增 `controllers/selection_controller.py`；改 `workspace.py`、`controllers/selections.py`、`app.py`（context property） | `pytest tests/`（全量） |
| S2 | QML 共享组件：`DraftOutputBar/RefineControls/MaskViewControls/ToolOptionsBar/TaskStatusBar`；`Main.qml` 属性迁绑 | 新增 `ui/components/selection/*`；改 `Main.qml`、`InspectorPane.qml`、`RegionPane.qml`、`CanvasPane.qml` | `test_redesign_ui.py`、`test_pixel_workflow.py`、`test_workspace_ui.py` |
| S3 | 新 SelectionPane 三步布局 + selectByText 单入口 + 副作用信号化 | 重写 `SelectionPane.qml`；改 `Main.qml`（Connections）、`controllers/objects.py`、`conversation.py` | UI 测试补：三步区块可见性、单按钮路由、输出条双处同源 |
| S4 | 收尾：菜单/ToolRail/LayersPane/AdjustmentPane 入口统一指向 `selection.*`；文案与 hint 全量核对；`qa_pixel_ui.py`/`qa_matting_ui.py` 脚本走查 | `ToolRail.qml`、`LayersPane.qml`、`AdjustmentPane.qml`、菜单 | 全量 pytest + QA 脚本 + 手工走查清单 |

新增测试建议：
- `tests/test_selection_controller.py`：工具状态迁移（chooseTool 副作用等价性）、`apply(mode)` 两分支、`refine("auto")` 路由表、task 状态机取消。
- UI 测试（沿用 `test_redesign_ui.py` 模式）：新面板 objectName 契约（保留现有 `selectionTargetSection` 等名称或同步更新测试）、输出条在两处渲染同一组件、禁用 hint 文案。

## 五、风险与取舍

| 风险 | 缓解 |
|---|---|
| Editor 门面 API 众多，QML/测试直接引用 `editor.*` | 旧槽保留薄委托一个版本期，测试先行迁移；grep `editor\.(draw|draft|selection|refine|matte|pixel)` 建清单逐个核销 |
| objectName 契约变更导致 UI 测试大面积红 | S2/S3 保留现有 objectName，新增控件用新命名；确需改名时同步改测试并在 commit 说明 |
| 工具状态从 QML 迁 Python 后，画布高频事件（拖拽采点）往返开销 | 拖拽中间态仍留 QML（`CanvasViewport` 本地累积点），仅释放时调 controller，与现状一致 |
| "智能细化"自动路由选错方法 | 下拉始终显示实际将使用的方法与原因，用户可手动覆盖；auto 逻辑写单测 |

## 六、验收标准

1. 选区功能全部经 `selection` 单一门面触发，`Main.qml` 不再持有选区工具状态。
2. 新面板按"做出选区/修边与检查/输出"三区组织；输出区文案说明后果；折叠区不再自动开合。
3. 文字选择只有一个按钮；修边只有一个主按钮 + 方法下拉；取消只有一个按钮。
4. RegionPane 与 SelectionPane 复用同一 `RefineControls`，matte radius 不再硬编码。
5. 全量 pytest 通过；`qa_pixel_ui.py`、`qa_matting_ui.py` 走查通过；分析文档 P1-P9 每项可在代码中指出对应消解点。

---

## 七、实施记录（2026-09-26 完成 S1-S4）

### 实际落地的文件

| 变更 | 文件 |
|---|---|
| 新增公共门面 | `src/iphoto/controllers/selection_controller.py`（`SelectionController`：工具/模式/笔刷/容差/蒙版显示状态、task 状态机、`refine(method)` 路由与 `refineMethods`、`apply(mode)`、`selectByText/selectByTextDirect`、`draftBegan/draftEnded/toolChosen` 信号） |
| Editor 接入与清理 | `workspace.py`：新增 `editor.selection` 属性；删除 `setAutoRefine/_auto_refine`、`selectionAction`、`setFeather`、`drawSelection`（双 API 收敛为草稿系） |
| 共享 QML 组件 | `ui/components/selection/`：`DraftOutputBar`（prefix 区分实例 objectName）、`RefineControls`、`MaskViewControls`、`TaskStatusBar`、`ToolOptionsBar` |
| 状态迁绑 | `Main.qml`：`selectionTool/selectionMode/brushRadius/tolerance/showMask/navigationTool` 改为 `selection.*` 只读绑定；`Connections.onChanged` 草稿副作用改为订阅 `selection` 的 `toolChosen/draftBegan/draftEnded` 三个显式信号；X/[/]/Q 快捷键与"载入蒙版/显示蒙版"改调门面 |
| 面板重写 | `SelectionPane.qml` 三区固定布局（①做出选区 ②修边与检查 ③输出），次要方式收进"更多方式"菜单；`RegionPane.qml` 复用 `RefineControls(regionMode)` 与 `TaskStatusBar`，删除硬编码 radius=8 |
| 输出条同源 | `InspectorPane.qml` 底部条替换为 `DraftOutputBar`（无 prefix，保留 `selectionToLayerButton/acceptSelectionButton/discardSelectionButton` 契约名）；面板内实例用 `pane` 前缀避免 findChild 歧义 |
| 新测试 | `tests/test_selection_controller.py`：工具状态与副作用、修边路由表、task 状态机取消、apply 两分支、maskView/showMask 联动 |

### 与设计稿的偏差（均为实测后调整）

1. **文字选择保留两个按钮**：`识别`（清单路由：有清单→targets，无清单→先建清单再匹配）+ `对话直选`（跳过清单的会话识别，原 directSelectionButton 语义）。"再点一次升级"的隐式交互在走查中不如显式按钮可发现，故保留显式次按钮但用 hint 说明差异；"画面元素"跳转移入"更多方式"菜单（P8/P9 消解）。
2. **取消按钮**：`TaskStatusBar` 按 `taskKind` 显示唯一取消（pixel/matte/ai 可取消；grabcut 本地计算不可取消并说明原因），替代原来"取消 AI 请求/取消当前计算"两按钮（P7 消解）。
3. **修边**：主按钮"智能细化"（`refine("auto")`：有草稿且像素模型可用→SAM 重识；否则 grabcut；否则 matte）+ "方法 ▾"下拉列出五种方法及可用性/适用场景/未配置原因；`按原图细化边缘`、`重新识别轮廓` 保留为可见按钮（QA 脚本与肌肉记忆依赖），全部走 `selection.refine`（P1 消解）。

### 顺手修复的既有缺陷（HEAD 上即失败，非本次引入）

- `LayersPane.qml`："图层属性" FoldSection 默认折叠导致改名/移组/透明度行永远不可见（`test_v14_ui` 两种尺寸均失败）。修复：默认展开，并把已声明未接线的 `compact` 属性接入 `detailsVisible`——草稿进行中或元素页时图层区自动收拢，为选区面板腾出滚动空间（1080×700 下 `checkCategoryButton`/`aiSelectionButton` 此前被挤出可视区）。
- 测试同步：`test_quality.py`（advice 过期断言按消息 id 查找，因 acceptSelection 会追加事件消息）、`test_canvas_ui.py`（`setProperty("selectionTool")` 改调 `selection.chooseTool`）、`test_layers/test_workspace_ui/test_quality` 中双 API 调用改走草稿流程。

### 回归结果

- `pytest tests/`：195 通过（含新增 6 个控制器用例）。
- `scripts/qa_matting_ui.py`：通过（真实 alpha 求解 + 新 RefineControls）。
- `scripts/qa_pixel_ui.py`：**失败，但在干净 HEAD 上以相同方式失败**（step1 Alt 排除点时 EfficientSAM 报"模型没有找到可靠目标"）；属模型/环境既有问题，与本次重构无关，未在本次范围内处理。
- 截图自查：`artifacts/redesign-idle-*.png`、`artifacts/redesign-draft-*.png`（1440×930 与 1080×700，QML 零警告）。
- `scripts/selftest_live_ui.py --live`（真实千问 qwen3.8-max + 真实 QML）：全流程通过——文字识别出天空草稿 → 智能细化 → 替换当前层蒙版 → 拉框新建调整层 → 撤销 → 对话自动分区（3 区）→ 分区内智能细化 → 确认创建分区图层 → 图层改名/移组 → 曝光滑杆拖拽与撤销 → AI 修图 → 建议模式；零 QML 警告，截图在 `artifacts/live-selftest/`。云端偶发失败（限流/瞬时错误）时状态保留、重试即恢复，自测脚本内置一次重试。

### 自测中发现并完成的打磨

- `document.number()` 报错带上具体值与区间（此前仅一句笼统文案，无法定位模型越界值）。
- AI 边界坐标越界（模型偶发返回 1000，约定 0~999；拒绝是既有测试契约，不改为钳制）在 `scene.parse_scene`、`ai_tasks.parse_selection/parse_regions` 处包装为可行动提示："…本次清单未建立；请重试或改用对话直选"等。
- 分区模式下修边菜单禁用"重识轮廓"（sam 对分区草稿静默无效），并注明"仅独立选区"。
- `SelectionController.setMaskView` 非法值不再改动 showMask。
- `CanvasPane` 对比按钮补 `compareButton` objectName，`scripts/verify_ui.py` 恢复可用（其 compare 断言顺序与现行为相反，已修正）；该脚本与自测脚本对 Repeater 委托控件改用视觉树遍历查找。

### P1-P9 消解对照

| 问题 | 消解点 |
|---|---|
| P1 四种修边并存 | `SelectionController.refine/refineMethods/refineAutoButton` + 方法下拉说明 |
| P2 输出语义模糊 | `DraftOutputBar` 后果说明文案 + 双处同源同组件 |
| P3 隐式副作用 | 副作用集中于 `chooseTool`/信号；`Connections.onChanged` 反向切页逻辑删除 |
| P4 多入口不一致 | 全部入口（ToolRail/菜单/工具栏/面板/图层缩略图）经 `workspace.chooseTool/reviewMask` → `selection` 门面 |
| P5 工具选项三处控制 | `ToolOptionsBar` 单组件绑定 `selection.*`；Alt/Shift 临时覆盖仍留在画布 |
| P6 灰了不解释 | 各按钮 `hint` 给出禁用/不可用原因（含能力未配置状态） |
| P7 两个取消 | `TaskStatusBar` 单一取消 + taskKind 状态机 |
| P8 文字选择三按钮 | 两按钮 + hint 路由说明（见偏差 1） |
| P9 面板跳转打断 | "画面元素"入菜单并保留返回路径说明（ScenePane 文案） |
