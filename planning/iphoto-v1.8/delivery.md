# iPhoto v1.8 交付与验收

对应计划：[plan.md](plan.md)。选区交互重构的分析与设计见 [../../doc/selection-analysis.md](../../doc/selection-analysis.md)、[../../doc/selection-redesign.md](../../doc/selection-redesign.md)。

## 交付内容

1. **选区操作重构**：`SelectionController` 门面 + 5 个共享 QML 组件 + 三步面板；P1-P9 全部消解（对照表见 redesign 文档）。
2. **内容感知填充**：`inpainting.py` + 图层 `inpaint` 字段 + 选区③按钮/选择菜单；OpenCV Telea/NS，能力表新增 `inpaint` 条目。
3. **修复画笔（J）**：图层 `heal` 笔画协议 + 画布工具链（ToolRail/顶栏/覆盖层/快捷键）；每笔一次历史提交。
4. **蒙版边缘位移**：mask `edge_shift`（-5~5，短边百分比），PIL 形态学实现，预览/导出/缩略图一致。
5. **一键自动调整**：worker `stats` + `auto_adjust.compute`（曝光/对比/阴影高光/冷暖/色偏），调整页"自动"按钮，尊重锁定参数。
6. **一键抠出主体**：u2net 直发 + draftBegan 链式建层，避免中间草稿误输出。
7. **架构**：worker op 注册表；`RASTER_CACHE` 蒙版栅格 LRU；合成缓存纳入 inpaint/heal 层。
8. **缺陷修复**：LayersPane 属性行不可见（HEAD 既有失败）、verify_ui 脚本、AI 坐标越界提示、分区修边菜单 sam 项、setMaskView 守卫。

## 验收证据

| 项 | 结果 |
|---|---|
| `pytest tests/` | 252+ 全绿（新增 test_selection_controller / test_auto_adjust / test_edge_shift / test_inpaint / test_heal / test_cutout / test_raster_cache） |
| `scripts/qa_matting_ui.py` | 通过 |
| `scripts/verify_ui.py` | 通过（含真实 AI 修图、滑杆锁定、对比切换） |
| `scripts/qa_live_qianwen.py --live --mode workflow` | 通过（qwen3.8-max：scene→targets→segment 全链） |
| `scripts/selftest_live_ui.py --live` | 全流程通过：A 选区/细化/输出两分支、B 分区、C 图层、D 对话修图与建议、E 自动调整/修复画笔/边缘位移/内容感知填充；零 QML 警告 |
| 截图 | `artifacts/redesign-*.png`、`artifacts/live-selftest/*.png` |

## 已知局限

- `qa_pixel_ui.py` step1（Alt 排除点）在 HEAD 上即失败：EfficientSAM 对该点报"没有可靠目标"。与本版无关，未处理。
- 内容感知填充为大孔洞（>约 15% 画面）时 Telea/NS 会留下模糊痕迹；提示文案已说明"分次小范围填充"。
- 自动调整为直方图规则，不理解语义；语义调整仍走 AI 对话/建议。
- 修复画笔与填充依赖 OpenCV（cv2）；缺依赖时按钮禁用并指向图像能力对话框。
- 云端偶发限流/瞬时错误会使单次 AI 步骤失败；状态保留、重试即恢复（自测脚本内置一次重试）。

## U1 用户指认缺陷修复轮（2026-09-26 第二轮）

1. **元素行 hover 预览缺失**：双重根因——`SceneIndex._hover` 预览图只在 `set_precise` 生成（新分析的元素没有预览），且行内 `HoverHandler` 不激活、跨行切换存在激活顺序竞态。修复：`set()` 为全部元素生成预览（`_preview_url` 共用）；行 hover 改用 `MouseArea{acceptedButtons: Qt.NoButton; hoverEnabled}` 的 entered/exited，且仅清除属于自己的 id。新增 `tests/test_scene_hover_ui.py`（窗口平台跑 hover 切换；offscreen 自动跳过；另含离线 preview 生成用例）。
2. **ComboBox 弹层高亮行白字白底**：新增 `components/SelectBox.qml`（显式深色 delegate/popup），替换 maskViewBox、sceneCategoryBox、groupTargetBox、chatModeBox 四处；`qa_matting_ui.py` 键盘链路复验通过，弹层截图 `artifacts/selectbox-popup.png`。
3. **点选失败弹错误打断叠加流程**：segment 因"没有可靠目标/未满足提示点"失败时不再弹错误，改为保留该次提示点（`pixel_selections.keep_points`）+ 信息提示"提示点已保留（共 N 个）…"，点击可继续叠加；其他 segment/matte 错误仍按错误上报。新增两条 bridge 级测试。
4. **文案与"暂存 vs 落点"逻辑**：面板头说明选区是独立暂存范围、③ 选择落点；按钮改为"点选 S / 框选 M / 按描述识别 / AI 直接识别 / 输出为新调整层 / 应用到当前层蒙版 / 放弃暂存"；进入暂存的 toast 改为"已进入暂存范围：…输出到图层后才会改变照片"；顶栏点选计数改为"已记 N/6 点"。

## U2 向导式选区模块与双模块开关（竞赛对照）

- 新组件 `ui/areas/SelectionGuide.qml`：三步卡片流（1 选范围：点选/框选/描述识别；2 修边缘：查看方式+智能修边+透明边缘+羽化；3 应用到：固定底部输出条，不随滚动消失），无菜单、无折叠区、无重复出口；高级工具指引到左侧工具栏与"元素"页。
- 旧面板 `SelectionPane.qml` 整体旁路为"经典"模式：`Main.qml` 的 `selectionUiMode`（默认 `guide`）+ 选区标签行小开关 `selectionUiToggle`（向导/经典）切换；两模式共用底部输出条与 `SelectionController`。
- objectName 契约：向导持有测试/脚本使用的名字（selectionDescriptionInput、aiSelectionButton、directSelectionButton、maskViewBox、refineMatteButton、refineAutoButton、inpaintButton、draftStateCaption），经典面板全部加 `classic` 前缀避免重名；`tests/test_selection_ui_modes.py` 覆盖默认模式、开关切换与焦点链路。
- 验收：`pytest tests/` 257 通过（默认向导模式）+ 1 跳过；`qa_matting_ui.py` 通过；`selftest_live_ui.py --live` 向导模式全流程通过；截图 `artifacts/guide-idle-1080.png`、`guide-draft-1080.png`、`classic-draft-1080.png`。

## U3 旁路模块第二版：元素 × 选区 × 调整合一（用户拒绝 U2 后重写）

用户反馈：U2 仍把元素、选区、调整割裂在三处；hover 能力没有用起来；"调整只针对选取"没有体现。重写 `SelectionGuide.qml`：

- **元素并入选区模块**：画面元素清单（分析/选同类/行列表）直接内嵌；行悬停=画布预览（悬停 MouseArea 置顶解决被行内按钮遮挡不触发的问题），行点击=设为当前范围，行内 ＋/− = 叠加/减去（`SelectionController.rowSelect`，add/subtract 无草稿时自动起暂存）。"元素"标签页在向导模式不再显示独立 ScenePane（经典模式保留，控件名加 `classic` 前缀防 findChild 重名）。
- **调整内嵌且只针对范围**："调整此范围"卡片含 6 条常用滑杆；按住滑杆时若存在暂存范围则 `autoLandIfNeeded()` 自动落地为局部调整层（一次可撤销提交），随后参数只作用于该范围；无暂存时作用于当前层并在卡片头标明作用对象。"是编辑同层还是新增层"的问题从界面消失。
- **工具芯片行**：点选/框选/套索/魔棒/画笔一行切换，当前工具高亮；文字描述与识别保留；修边卡片紧凑化（查看方式+智能修边+半径+透明边缘+羽化）。
- 输出仍为底部固定输出条（不随滚动消失），另保留内容感知填充入口。
- 新增测试：`rowSelect` 叠加语义、`autoLandIfNeeded` 建层；`test_scene_hover_ui` 继续覆盖行悬停切换。
- 验收：`pytest tests/` 259 通过 + 1 跳过；窗口平台 hover 测试 2 通过；`qa_matting_ui.py` 通过；`selftest_live_ui.py --live` 全流程通过；截图 `artifacts/module-elements-hover.png`（悬停预览叠加）、`module-draft-1440.png`、`module-landed-1440.png`。

## U4 向导第三版：调整归图层、悬停出滑杆（用户否决 U3 后重做）

用户裁定："调整是针对图层的，hover 到图层才显示；选取和元素合并"。U3 的内嵌调整卡与自动落地废除：

- **向导模块只管范围**：工具芯片行 + 文字识别 + 元素清单（悬停预览/点击/＋/−）+ 修边卡 + 主按钮 `生成本地调整层`（`guideToLayerButton`，一次可撤销落地）；不再有内嵌滑杆，也不再自动落地。
- **调整 dock**：`LayersPane` 列表上方固定 54px dock；悬停任一调整层行即出现六条滑杆（曝光/对比/冷暖/高光/阴影/饱和），只写该层 recipe 并把键加入该层 locked；松开提交一次历史（可整笔撤销）。悬停状态带 160ms 清除延时，dock 自身悬停可续期，避免布局抖动与点击拦截。
- 控制器新增 `layerRecipe/setLayerParameter/finishLayerGesture/revision`；删除 `autoLandIfNeeded`。
- 验收：`pytest tests/` 260 通过 + 1 跳过；窗口平台 hover 测试通过；截图 `artifacts/v4-module-range.png`、`v4-layer-dock.png`（悬停层行 dock 出滑杆）；`qa_matting_ui.py` 与 `selftest_live_ui.py --live` 复跑通过。

## U5 导航重构：删标签页，状态驱动（用户第四次指正后）

用户裁定："三个按钮（调整/元素/选区）都删掉；点击图层就显示调整内容，取消选中图层就是元素和选取；两者功能本来就能合并。"

- **删除**：InspectorPane 标签行（调整/元素/选区 + 向导开关）、`SelectionPane.qml`（经典）、`ScenePane.qml`、`selectionUiMode/inspectorPage/inspectorView/lastTab/hadRegion` 全部状态；`test_selection_ui_modes.py` 改写为导航测试（断言标签按钮不存在）。
- **新导航**：`SelectionController.pickedLayerId`（`pickLayer/clearPick`）。未选中图层 → 统一范围模块（工具芯片+文字+元素清单 hover 预览/点击/＋/－+修边+生成本地调整层）；选中图层 → AdjustmentPane（头部新增"返回范围"按钮 `unpickButton`）。图层行点击=选中/再点=取消；草稿开始自动取消选中（范围优先）。
- **输出即选中**：`apply(new_layer/replace_mask/inpaint)` 与 `applyRegions` 落地后自动 pick 新层，右侧立即显示该层调整滑杆——"范围→调整"零切换。
- 图层区在范围模式压缩高度（列表可点选、属性区隐藏），小窗下模块关键控件不被挤出。
- 修复 SelectBox/RowLayout 隐式宽度把 inspector 撑到 518px 的布局 bug（`implicitWidth` 上限 + 行 `implicitWidth: 0` + 类别框固定宽）。
- 验收：`pytest tests/` 261 通过 + 1 跳过；截图 `artifacts/v5-range-mode.png`、`v5-picked-mode.png`；`qa_matting_ui.py` 通过；`selftest_live_ui.py --live` 复跑（填充步等待上限提至 120s：真实照片+修复笔画叠加时单次渲染含多次 cv2.inpaint，耗时可超 12s）。

## M 轮：右键菜单取代按钮堆（用户指认"页面臃肿、图层不能删"）

- **图层行右键**：编辑此层 / 载入蒙版为范围 / 重命名 / 复制层 / 显隐 / 上移 / 下移 / 编组 / 移出组 / 移入组子菜单 / 删除（可撤销）。属性区随之删除"移入组"整行与 ↑↓删 按钮，只留名称+透明度；`test_v14_ui` 与自测的移出组改走菜单。
- **元素行右键**：设为范围 / 加入范围 / 从范围减去；行内 ＋/− 按钮删除，行只剩名字（hover 预览保留）。
- **画布右键**：全选 / 反选 / 清空范围 / 取消选区 / 显示隐藏蒙版 / 原图对比 / 适应 / 100%。
- **调整滑杆行右键**：解锁此项 / 重置此项；行内"解锁"按钮删除，锁定状态改为一行小字提示。
- **对话消息右键**：复制内容 / 应用建议；消息行"复制"按钮删除。
- 顺带修复 Basic 风格菜单"高亮+禁用"行白底浅字：窗口 palette 补 light/mid/midlight/dark 暗色值（`Main.qml`）。
- 新增 `tests/test_context_menus.py`（图层删除/复制/undo、组外移出组禁用、元素行加入范围、画布反选/取消、滑杆解锁/重置）5 例。
- 验收：`pytest tests/` 266 通过 + 1 跳过；`qa_matting_ui.py` 通过；`selftest_live_ui.py --live` 全绿零警告（C2 走图层右键菜单）；截图 `artifacts/v6-layer-menu.png`、`v6-canvas-menu.png`。

## R 轮：导入/导出/预览/点选四项硬伤（用户真实照片反馈）

1. **导入失败根因 = MPO**：微信/手机多帧 JPEG 被 PIL 识别为 MPO，旧白名单 {JPEG,PNG} 拒绝。现接受 MPO；用户原图（6016×4016、EXIF 旋转 8）实测 5.6s 导入成功并正确转竖幅。
2. **RAW 支持**：`engine._load_raw` 走 rawpy（相机白平衡→sRGB 8bit），扩展名覆盖 CR2/CR3/NEF/ARW/DNG/RAF/ORF/RW2/PEF/SRW/X3F；缺 rawpy 时提示安装命令；能力表新增 `raw` 条目；导入对话框新增 RAW 过滤器；rawpy 进 requirements.txt（.venv 已装 0.27.1）。
3. **导出强化**：`components/ExportDialog.qml` 取代裸 FileDialog——路径+浏览、格式（JPEG/PNG）、JPEG 质量滑杆 80–100（默认 92）、后缀自动纠正、不覆盖原图提示；`exportWithQuality` 走 worker export 的 jpeg_quality。测试覆盖对话框导出 jpg/png 与 q80<q100 体积差。
4. **元素预览不再是框**：scene 分析完成后 `pixel_selections.precache` 后台对全部元素跑精确分割（segment purpose=precache，worker 对 precache 逐元素容错跳过失败者），`set_precise` 用真实蒙版重写 hover 预览；实测 11/11 元素预计算完成，行点击成草稿 0.1s（原 3–5s）。
5. **点选预热**：`chooseTool("smart")` 启动独立短进程编码代理照片，`EfficientSAM.warm` 只编码不解码，嵌入落盘缓存；画面 AI 分析也与此预热并行。点选等待编码时明确显示排队状态并允许取消。以前的 `segment jobs=[]` 主 worker 预热会阻塞画框和换图，已改由独立进程处理。
6. **后台任务优先级**：元素 precache 按对象拆成低优先级请求；`worker_bridge._pump` 让交互请求与预览在对象之间插队。独立预热期间主 worker 仍可渲染、导入和导出，像素分割等嵌入就绪后再发给主 worker，避免双重编码。`Editor.busy` 不计后台预热和 precache，但计排队中的用户点选。
7. **勾选回归修复（用户指认"操作都不通了"）**：M2 删＋/−时误删元素行勾选框，批量组合断链。恢复行首 CheckBox（`sceneCheck_<id>`），与"点击=设为范围、右键=加/减"并存；组合条（新选区/添加/减去/相交）恢复可用。新增 `tests/test_elements_flow.py`（勾选驱动组合、恢复清单触发 precache）。
8. **precache 触发面补全（用户指认"预览还是矩形"）**：此前仅云端 scene 响应触发预计算；**清单从本地缓存恢复（analyzeScene 命中 remember）与项目打开恢复**两条路径不触发，导致悬停永远是框。现三处均触发；行 hint 标明"真实轮廓已就绪/预计算中"。
9. **AI 边界坐标容错**：qwen3.8-max 的 scene box 会给出 -1/1000 边界哨兵值，此前整单拒绝导致"分析画面"完全失败（用户"操作都不通了"的另一根因）。新增 `document.coord999`：[-16,1015] 容差带内钳制到 [0,999]，超出（如 2000 像素值）仍按尺度错误拒绝；grounding 与 ai_tasks 的 0~999 解析统一改用它。实测 max 10 对象建清单 16.7s + precache 15s + 勾选组合 0.0s（缓存命中）。
10. **worker 协议对杂散 stdout 容错（用户截图"处理响应失败：Expecting value: line 1 column 2"）**：根因是 worker 进程里库日志（ONNX runtime 的 `[W:onnxruntime:…]`，以 `[` 开头）混入 stdout，被当 NDJSON 响应解析。`worker_bridge._read` 现在跳过所有不以 `{` 开头的行并转发到主进程 stderr；回归测试 `test_stray_worker_stdout_lines_are_ignored` 覆盖。
11. **大图交互性能（用户"点新选区没效果，估计图片太大了"）**：worker 的 `proxy` 此前是全分辨率源图，24MP 照片上每次渲染/叠加/经典选区都在 24MP 上跑，点新选区后分钟级无反馈。现 `open` 生成 1600px 预览代理：render/overlay/segment/selection(grabcut/wand/u2net) 全走代理，**导出与 alpha matting 仍全分辨率**；画布底图也存代理 PNG（开图更快）。用户 24MP 微信图实测：开图 5.6s→1.2s，新选区/画框叠加 0.0s，首次点选 6.5s 出草稿，零错误。回归测试断言预览 ≤1600 且导出保持 3200×2400。
- 新增 `tests/test_import_export.py` 7 例（MPO/RAW/缺 rawpy 提示/导出对话框/warm 请求/点数状态/precache）。
- 验收：`pytest tests/` 273 通过 + 1 跳过；`qa_matting_ui.py` 通过；截图 `artifacts/v7-export-dialog.png`。

## 复跑方式

2026-09-27 的新一轮真实使用审查、问题头、调度架构修改与验证见 [ux-audit-2026-09-27.md](ux-audit-2026-09-27.md)。
后续最小窗口与导出路径修复已完成，最终回归为 `285 passed, 1 skipped`；详细截图和操作记录仍见上文审查文档。

```
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe scripts\qa_matting_ui.py
.venv\Scripts\python.exe scripts\verify_ui.py
.venv\Scripts\python.exe scripts\selftest_live_ui.py --live   # 需 Windows 凭据中的千问配置
```
