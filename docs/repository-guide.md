# iPhoto 源码仓库说明

本项目是 Python 3.12 + PySide6 / Qt Quick 的原生桌面照片编辑器。照片合成与本地分割运行在独立工作进程，云端 AI 返回经过校验的参数、对象位置或内部点。运行入口为 `run.py`，应用版本 1.6.0，项目格式为 1.5。

## 从哪里开始

```powershell
# 已配置好的本机环境
.venv\Scripts\python.exe run.py

# 新环境请先执行现有安装脚本，再安装开发工具
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src --select F
.venv\Scripts\python.exe -m ruff format --check src
```

`start-iphoto.cmd` 用于日常启动；`--empty` 启动空文档；`--capture 路径 --quit-after 毫秒` 用于本应用的截图验收。

## 目录与职责

| 文件 / 目录 | 职责 | 不应加入的内容 |
|---|---|---|
| `src/iphoto/app.py` | Qt 生命周期、字体、窗口加载、启动参数 | 修图规则和控件业务 |
| `workspace.py` | 唯一的 Editor QObject，Qt 属性、信号、槽和状态所有权 | 新的大段业务逻辑 |
| `controllers/worker_bridge.py` | 工作进程队列、合并预览请求、结果代际校验 | 图像算法 |
| `controllers/layers.py` | 图层、组、历史、手动参数、图层缩略图 | 网络请求解析 |
| `controllers/selections.py` | 独立选区草稿、笔画、修边、输出及分区确认 | 云端供应商配置 |
| `controllers/objects.py` | 元素清单、文字目标流程、点选和组合 | Qt 控件布局 |
| `controllers/pixel_selections.py` | 像素任务、提示点、对象缓存与草稿事务 | 分割算法、模型下载 |
| `segmentation/` | 独立定位协议、点/框、ONNX 推理、候选筛选、边缘处理、传统算法 | Qt 控件、云端凭据、项目写入 |
| `controllers/conversation.py` | AI 请求上下文、建议绑定、对话记录、结果应用 | 任意模型代码执行 |
| `controllers/session.py` | 项目打开、保存、恢复、关闭保护、导出 | 绘制控件 |
| `document.py` | 文档校验、蒙版光栅化、组与图层合成、项目序列化 | Qt 窗口或 API Key |
| `layer_tree.py` | 父组关系校验、层级视图、后代集合、子树复制 | 图像 I/O |
| `scene.py` | 元素/目标协议、清单校验、集合运算、点选索引 | 网络和 Qt |
| `ai_tasks.py` | 轮廓与分区协议、规范化与严格验证 | 自动修改图层 |
| `ai_protocol.py` | AI 请求格式、缩略图编码、调色结果解析 | 凭据保存、网络生命周期 |
| `ai.py` / `ai_settings.py` | 异步 HTTPS；供应商设置与 Windows 凭据管理 | 日志输出 Key |
| `worker.py` | 有界 NDJSON 请求、图像加载/导出/合成/分割 | GUI 访问 |
| `engine.py` | 色彩管理、LUT 调色、空间滤镜、导出约束 | 图层 UI |
| `viewport.py` | 独立画布几何、缩放锚点、DPI 换算、平移边界、临时按键状态 | 编辑历史、项目序列化、worker 调度 |
| `preview_cache.py` | 嵌套合成缓存、稳定视觉键、内存预算 | 跨源图片复用旧缓存 |
| `plugins.py` | 能力元数据与处理器注册、本地算法适配 | 从项目文件加载任意 Python |
| `masks.py` / `storage.py` / `paths.py` | 有界 PNG 蒙版、原子写入、路径转换 | 编辑流程 |
| `ui/Main.qml` | 顶层工作区、快捷键、菜单、文件对话框 | 各面板的完整实现 |
| `ui/areas/` | 画布、工具栏、调整、选区、元素、分区、图层、对话、检查器 | 图像算法和凭据处理 |
| `ui/components/` | 共享按钮、滑杆、输入框、说明文本、主题 | 文档修改逻辑 |
| `tests/` | 离线测试、模拟 HTTP、真实 Qt 鼠标键盘测试 | 自动读取个人云端 Key |
| `scripts/` | 安装、显式真实 API 验收、性能测量 | 应用启动时偷偷运行的任务 |
| `planning/iphoto-v1.4/` | 本轮计划和验收记录 | 运行时数据 |

## 状态和依赖约定

`Editor` 持有一份文档状态；`controllers` 是按功能组织的动作函数，首个 `self` 参数由 Editor 显式传入。Qt 槽保留在 Editor 中，函数委托到对应模块。这样 QML 与测试入口稳定，同时避免 PySide 多层 QObject 继承与重名 Property 引发的元对象冲突。

新的状态字段在 Editor 构造时初始化，源图片切换时由 `session._opened` 重置；需要恢复的字段同时补入 `session._payload` 和 `document.validate_project`。只产生通知的导航动作不要增加照片编辑历史。现阶段控制器共享 Editor 状态，后续继续拆分时可以引入专门的文档状态对象，但不要复制多份互相不同步的图层列表。

QML 区域必须显式接收 `workspace`（界面状态）和 `editor`（业务接口）。禁止跨文件引用别的区域内部 id；用 `focusPrompt()`、`commitText()` 等小接口连接区域。测试需要稳定的 `objectName`。模型生成文本必须使用 PlainText 显示。

## 三条主要数据流

1. **文字选区**：`SelectionPane → selectByDescription → objects → pixel_selections → worker segment → segmentation/service`。没有清单时调用 scene 返回框/内部点；随后 targets 只发送文字和对象 id。每个对象先生成像素蒙版，再组合成草稿；已有像素结果复用缓存。S 点选跳过云端。清单缺少目标时可“直接识别此目标”。
2. **编辑预览**：Qt 槽 → 对应 controller → 状态/历史 → 90 ms 合并计时器 → worker NDJSON → 校验图层 → 合成缓存 → PNG → QML Image。只有最新代际的结果可以进入画面。
3. **输出**：草稿先确认到调整层或组蒙版，再用新文件名导出。保存项目保留源路径、SHA-256、图层、组、蒙版、清单、对话和未确认草稿；打开时验证原照片没有变化。

## 画布模块（1.4.1）

`Editor.viewport` 持有独立 `Viewport(QObject)`。成功加载照片时，worker bridge 把原图尺寸传给 `setSource`；QML 把窗口的 DIP 尺寸和屏幕 DPR 传给 `resize`。缩放比例 1.0 对应原图像素与物理屏幕像素一对一，图像在 QML 中的宽度为 `source_width × zoom / DPR`。渲染源仍是最长边 1600px 的代理预览；未来原分辨率瓦片必须另设质量与异步结果管理，不能把放大代理图称为高清视图。

- `CanvasPane.qml`：画布上下文、百分比输入、适应/100%/导航按钮、帮助菜单与对话面板组合。
- `CanvasViewport.qml`：图像与蒙版定位、选区鼠标事件、比较分割线、导航手势路由。优先处理中键/空格/抓手/缩放，普通选区事件继续交给下层；不通过切换选区工具实现临时抓手。
- `CanvasOverlays.qml`：把对象轮廓和笔迹映射到屏幕坐标，纹理尺寸始终等于可见画布；不可重新放回大尺寸 photo Item 中分配 Canvas。
- `CanvasNavigator.qml`：缩略图与可视矩形、点击/拖动定位；复用已有预览，不生成新的图像。
- `viewport.py`：缩放锚点不漂移；窗口重排保持源图中心；平移保留可见边缘；窗口失焦/输入文字/打开弹窗时清理临时键与手势。只有本窗口事件会被过滤。

导航不进入项目/撤销，也不向 worker 提交任务。选区点仍用相对于完整 photo 的归一化坐标；缩放、窗口重排、空格切换发生在笔画中途时，取消未提交笔画，避免把两个坐标系拼成错误选区。

`test_viewport.py` 覆盖几何与高 DPI；`test_canvas_ui.py` 使用真实 Qt 键鼠/滚轮事件，检查输入焦点、草稿不变、坐标对应、导航图、比较线和两种窗口尺寸。`scripts/benchmark_canvas.py` 单独测量软件渲染帧及同步读回时间，不能当作显示刷新率或真实 FPS。

## 图层组的确切语义

项目仍使用有界的平铺记录列表，每项有稳定 `id`、`kind`（adjustment/group）和 `parent_id`，从这些关系构造真实树。相同父节点的列表顺序是从下到上；界面反向显示。支持四级组嵌套，合计 32 项。拒绝循环父链、不存在的父节点、普通调整层作为父节点及重复 id。

组内各层先对进入该组的照片依次调整，再将组结果通过组蒙版和不透明度与进入组前的照片混合。组不透明度只应用一次。隐藏父组会隐藏所有后代；组蒙版外保持进入组前的像素。组没有直接调色参数，须在组内新建调整层。

这是调整图层组；尚未实现 PSD 导入、独立像素图层、智能对象、剪贴蒙版和 Photoshop 的所有混合模式。折叠仅改变显示，不影响合成；复制组会重新分配所有后代 id；删除组包含后代，整次动作可撤销。

## 添加一项能力

1.5 的像素后端入口是 `segmentation/service.segment`。当前 `EfficientSAM.predict` 接收 PIL 图像和原图尺度的坐标/标签，返回候选 logits、模型评分和计时；服务负责约束检查及位图编码。`models.py` 固定权重清单，`runtime.py` 处理 Windows C++ DLL 版本一致性；两者不触碰系统文件。`classical.py` 保存原 GrabCut/魔棒/U²-Net 实现，`plugins.py` 保留元数据和旧函数兼容出口。完整依据与边界见 [分割模块设计](../planning/iphoto-v1.5/selection-research.md)。

`test_pixel_selection.py` 测协议、提示约束和位图边界；`test_pixel_workflow.py` 测真实 Qt 输入路由及取消/过期/失败保护。旧协议事务测试显式使用 `pixel_protocol_stub`，不计入模型质量证据。`test_v14_ui.py` 和 `test_redesign.py` 的对应案例在权重存在时通过真实 worker 推理；首轮 CPU 编码使用 60 秒测试期限。自然照片对照用 `scripts/qa_pixel_selection.py`，几何真值测量用 `scripts/qa_segmentation_geometry.py`，二者均为真实模型。

- **界面区域**：在 `ui/areas/` 增加组件，显式绑定依赖；仅将布局组合写进 Main/Inspector。
- **编辑动作**：在所属 controller 中实现，Editor 增加简短 Slot；通过 `_can_edit()`、`_commit()`、`_change()` 遵守草稿、历史和渲染规则。
- **本地选区后端**：在 `plugins.py` 添加 Capability 元数据以及 `SELECTION_BACKENDS[id]` 适配器。统一调用边界为 `(PIL.Image, validated_mask, options) → (validated_mask, quality)`。后端在 worker 运行，依赖延迟导入。记录代码/权重来源、许可证、文件校验和、CPU/GPU要求；模型下载应有独立安装入口。仅添加元数据而没有处理器会明确报错，不会误调其他模型。
- **AI 平台/协议**：平台地址与 Key 规则加入 `ai_settings.py`；请求差异进入 `ai_protocol.py`；结果解析必须有界、校验类型、完成状态、坐标/参数/id。模型只返回数据，不能提供本地可执行代码。
- **文档字段**：先定义迁移和验证规则，再补保存/恢复与正反例测试。1.5 可读取 0.1、1.2、1.3、1.4 项目；老版本不应覆盖新格式项目。

## 测试与调试

`test_v14.py` 覆盖对象协议/组合、图层组数学、非法层级、缓存一致性、项目恢复。`test_v14_ui.py` 用真实 Qt 鼠标键盘检查 1440×930 和 1080×700；截图标注 mock，因为照片对象来自测试清单。旧测试继续覆盖原图保护、导出、锁定参数、凭据、恢复失败、AI 中断和 UI 快捷键。

```powershell
# 离线；不读取真实云端 Key
.venv\Scripts\python.exe -m pytest -q

# 独立运行，避免与测试/导出争用 CPU
.venv\Scripts\python.exe scripts\benchmark_v14.py

# 仅在明确授权真实云端调用后运行；读取系统凭据，参数中不接受 Key
.venv\Scripts\python.exe scripts\qa_live_qianwen.py --live --stage workflow --mode workflow
```

真实 API 验收只使用内置湖景，不上传用户私有照片。Windows 沙箱可能无法访问登录用户的凭据，应在同一用户的正常 Windows 会话执行。记录请求类型、延迟、HTTP/完成状态、经脱敏的结果与覆盖图；不记录 Authorization、Key 或完整图片请求体。`workflow_cached` 模式需先有 `artifacts/v14-live-workflow/real-workflow.iphoto`。

性能报告区分 CPU 合成和真实云端耗时；合成数值不包含 PNG、进程通信与界面显示。64 MiB 是缓存的逻辑像素预算，不是整个应用的内存上限。对象悬停使用 384×384 索引，不做逐帧分割或云端请求。


## 1.6 透明边缘模块

`matting/trimap.py` 管前景 / 背景 / 未知约束，`matting/solver.py` 只管有资源边界的 CPU 数值求解，`matting/service.py` 管文档输入输出。`controllers/matting.py` 管草稿事务；`worker.py` 的 `matte` 操作调用服务，UI 不导入数值后端。`segmentation/` 继续负责对象语义与初选，两者不能混为一类模型分数。

新灰度 PNG 可带 `sampling: "alpha"`。`masks.py` 统一处理插值及零覆盖保护，`document.raster_mask` 统一处理手工修正和内羽化。项目 schema 1.5 接受旧格式；修改资产字段须同时检查验证、保存恢复、画布预览和原图尺寸导出。

“调色效果”预览只在 `controllers/worker_bridge._schedule_render` 的深拷贝图层里替换草稿蒙版。不要把临时预览写回真实层，否则会破坏撤销和取消。

真实求解、半透明边缘与小孔洞测试在 `tests/test_matting.py`。`scripts/qa_alpha_matting.py --natural` 生成已知 alpha 和真实山林对照；`scripts/qa_matting_ui.py` 通过真实 Qt 输入触发求解和预览。输出在 `artifacts/alpha-matting/`。范围、性能、未实现的颜色补偿见 [1.6 技术说明](../planning/iphoto-v1.6/alpha-matting.md)。
