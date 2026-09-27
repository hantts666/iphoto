# iPhoto v1.8 计划：选区体验重构 + Photoshop 级补齐

指导思想：向"AI 版 Photoshop"演进——交互上把迷幻的操作收敛为任务流，功能上补齐
Photoshop 的核心修图动作（内容感知填充、修复画笔、边缘位移、自动调整、一键抠出），
架构上把选区状态收敛为单一门面、把 worker 分发注册表化、给重复渲染加栅格缓存。

## A. 选区面板重构（已完成，见 ../../doc/selection-redesign.md）

- `SelectionController`（`editor.selection`）：工具/模式/笔刷/容差/蒙版显示/任务状态机的单一事实源；
  `draftBegan/draftEnded/toolChosen` 是视图联动的唯一信号。
- 面板三区固定布局：①做出选区 ②修边与检查 ③输出到图层；输出条组件双处同源。
- 删除直接改层的双 API 与死代码；`Main.qml` 不再持有选区工具状态。

## B. 新功能（Photoshop 对齐）

| 功能 | 入口 | 实现 | 非破坏性 |
|---|---|---|---|
| 内容感知填充 | 选区③"内容感知填充（移除选区内容）"、选择菜单 | 图层 `inpaint:{method,radius}` 字段 + `inpainting.py`（cv2 Telea/NS），渲染时先填充再套 recipe | 是：独立填充层，删层即还原 |
| 修复画笔 J | ToolRail"修复"、选区"更多方式" | 图层 `heal:{ops:[{kind:heal,points,radius}]}`，每笔一次提交可逐笔撤销 | 是：笔画存协议，渲染时合成 |
| 蒙版边缘位移 | 选区②"边缘位移"滑杆（-5~5，短边%） | mask 协议 `edge_shift`，`raster_mask` 用 PIL Min/MaxFilter 收缩/扩张 | 是：协议字段，全链路一致 |
| 一键自动调整 | 调整页"自动" | worker 渲染附 `stats`（通道均值+亮度分位数），`auto_adjust.compute` 本地直方图规则 | 是：一次可撤销提交 |
| 一键抠出主体 | 选区"更多方式 → 一键抠出主体" | u2net 请求直发（不建中间草稿）→ draftBegan 链式 `selectionToLayer` | 是：新建图层 |

协议变更（向后兼容）：mask 新增可选 `edge_shift`（int -5~5）；layer 新增可选
`inpaint`、`heal`。旧项目无这些字段即默认值；`validate_layers/validate_mask` 校验新字段。

## C. 架构迭代

- `worker.py` 主循环改为 op 注册表（`register("open"/"render"/"selection"/"segment"/"matte"/"interpret"/"export")`），新增 op 只需注册一个函数。
- `document.RASTER_CACHE`：蒙版栅格 LRU（64 MiB 预算，按像素计权淘汰），`render_nodes` 走 `raster_mask_cached`；worker `open` 时清空。蒙版字典按版本不可变，json(除 label)+size 作 key。
- `preview_cache.node_key` 纳入 `inpaint/heal`，零 recipe 的填充/修复层也进合成缓存。

## D. 质量与既有缺陷修复

- 修复 HEAD 上即失败的 `test_v14_ui`：LayersPane"图层属性"行被折叠区吞没；默认展开并把 `compact`
  接入 `detailsVisible`（草稿/元素页自动收拢图层区，为选区面板腾空间）。
- `verify_ui.py` 修复（compareButton objectName、compare 断言顺序）。
- AI 边界坐标越界（模型偶发 1000）保留拒绝契约，但错误信息带值与区间，并包装为可重试提示。
- 新增脚本 `scripts/selftest_live_ui.py --live`：真实 QML + 真实千问全流程自测（含 v1.8 新功能阶段 E）。

## E. 验收

- `pytest tests/` 全绿（252+ 用例，含新增 5 个测试文件）。
- `qa_matting_ui.py`、`verify_ui.py` 通过；`selftest_live_ui.py --live` 全流程通过、零 QML 警告。
- 已知既有问题：`qa_pixel_ui.py` step1 在 HEAD 上即失败（EfficientSAM 对 Alt 排除点报无可靠目标），与本版无关。

## F. 后续候选（未做）

- 曲线/色阶工具、图层混合模式、RAW 解码、超分辨率 upscale、LaMa 级大孔洞填充、
  人脸修饰频率分离、GPU 渲染后端。均已在仓库指南"不应加入"边界外评估过工作量。
