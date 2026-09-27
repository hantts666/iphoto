# iPhoto 源码仓库说明

本项目是 Python 3.12 + PySide6 / Qt Quick 的原生桌面照片编辑器。照片合成与本地分割运行在独立工作进程，云端 AI 返回经过校验的参数、对象位置或内部点。运行入口为 `run.py`，应用版本 1.7.0，项目格式为 1.7。

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
| `controllers/worker_bridge.py` | 主工作进程队列、合并预览请求、结果代际校验、独立预热与像素进程生命周期 | 图像算法 |
| `controllers/detail_tiles.py` | 原图细节视口请求合并、裁剪范围、独立细节进程与过期结果隔离 | 图层算法 |
| `controllers/matte_process.py` / `matting/worker.py` | 原图透明边缘的独立进程、快速取消与结果代际隔离 | 主编辑 worker 的 UI 状态 |
| `controllers/export_process.py` / `export_worker.py` | 原图导出的独立一次性进程、取消、暂存清理与最终发布 | 主编辑预览队列 |
| `controllers/layers.py` | 图层、组、历史、手动参数、图层缩略图 | 网络请求解析 |
| `layer_rows_model.py` | 图层侧栏的稳定 Qt 行模型，按变化行发更新 | 文档和图像处理 |
| `conversation_rows_model.py` | 对话侧栏的增量 Qt 行模型，追加消息只插入一行，建议状态只刷新对应行 | 对话序列化和 AI 调用 |
| `controllers/selections.py` | 独立选区草稿、笔画、修边、输出及分区确认 | 云端供应商配置 |
| `controllers/selection_controller.py` | 选区 UI 单一门面：工具/模式/笔刷/容差/蒙版显示状态、任务状态机、修边路由与输出分支 | 直接改蒙版数据（仍委托 selections 等模块） |
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
| `worker.py` | 有界 NDJSON 请求、图像加载与预览合成；保留旧导出和分割协议兼容入口 | GUI 访问 |
| `segmentation/warm_worker.py` | 按需在独立短进程编码代理图并落盘缓存 | Qt 状态、图层编辑 |
| `segmentation/pixel_worker.py` | 独立持久进程处理交互点选与低优先级元素轮廓；可结束并重启 | Qt 状态、图层编辑 |
| `detail_worker.py` | 按可见区域在原图像素上合成图层，返回带位置的细节 PNG | Qt 状态、画布导航 |
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

`Editor` 持有一份文档状态；`controllers` 是按功能组织的动作函数，首个 `self` 参数由 Editor 显式传入。Qt 槽保留在 Editor 中，函数委托到对应模块。这样 QML 与测试入口稳定，同时避免 PySide 多层 QObject 继承与重名 Property 引发的元对象冲突。选区交互状态（工具、模式、笔刷、容差、蒙版显示、任务种类）是例外：由 `SelectionController`（`editor.selection`）这个单独的 QObject 持有，QML 只读绑定并经它下发，避免窗口级属性与面板各自为政；`draftBegan/draftEnded/toolChosen` 是视图联动的唯一信号来源。

新的状态字段在 Editor 构造时初始化，源图片切换时由 `session._opened` 重置；需要恢复的字段同时补入 `session._payload` 和 `document.validate_project`。只产生通知的导航动作不要增加照片编辑历史。现阶段控制器共享 Editor 状态，后续继续拆分时可以引入专门的文档状态对象，但不要复制多份互相不同步的图层列表。

QML 区域必须显式接收 `workspace`（界面状态）和 `editor`（业务接口）。禁止跨文件引用别的区域内部 id；用 `focusPrompt()`、`commitText()` 等小接口连接区域。测试需要稳定的 `objectName`。模型生成文本必须使用 PlainText 显示。

图层行由 `layer_tree.display_rows` 生成轻量摘要，`layer_rows_model.LayerRowsModel` 作为 QML 的稳定 `QAbstractListModel`：行顺序/id 不变时只发送变动行的 `dataChanged`，结构变化才重置列表。`Editor.layers` 的列表属性保留给旧接口。`maskToken` 取蒙版字典身份：正式蒙版的编辑、项目恢复与撤销均替换字典，因此即使标签不变，也会触发 `layersChanged`；普通状态或透明度变化不会重取缩略图。`LayersPane` 对已显示的蒙版版本做本地比较，只有版本变化或新行进入视口时读取缩略图。若以后引入原地修改正式蒙版，必须同时改为显式蒙版版本号或确保该修改能推进 token。

## 三条主要数据流

1. **文字与对象选择**：`SelectionGuide → selectByDescription → objects → pixel_selections → pixel_worker → segmentation/service`。没有清单时调用 scene 返回框/内部点；随后 targets 只发送文字和对象 id。每个对象先生成像素蒙版；已有像素结果复用缓存。批量勾选的主操作带 `auto_apply` 意图，结果返回后一次性建立局部调整层并切到参数面板，失败时保留原图层；手动组合仍生成可修边的草稿。S 点选跳过云端。清单缺少目标时可“直接识别此目标”。
2. **智能修图对话**：默认 `auto` 协议用严格字段的 `action` 决定调整当前图层、自动建立1～4个局部层、只回答或说明不支持。`parse_auto` 复用配方与分区验证；局部结果经 `pixel_selections.select_regions(auto_apply=True)` 送入独立像素工作进程，全部蒙版完成后才原子创建图层并一次提交历史。Qwen 的 JSON Object 响应可能给出数组或越界参数；提示包含完整参数范围，客户端校验失败时将具体原因加入系统提示并仅重试一次，重试期间维持忙碌状态，仍失败则不改照片。画面分析 `scene` 和分区规划共用此修正路径。模型若沿用旧协议要求用户先手选磨皮范围，对话可再调用现有 `regions` 规划并沿同一自动建层路径执行。明确选择“分区预览”时仍保留检查草稿和手动确认。
3. **编辑预览**：Qt 槽 → 对应 controller → 状态/历史 → 90 ms 合并计时器 → worker NDJSON → 校验图层 → 合成缓存 → PNG → QML Image。只有最新代际的结果可以进入画面。
4. **输出**：手动草稿点“开始调整此范围”后创建局部层；批量对象选择从主操作直接创建。随后用新文件名导出。保存项目保留源路径、SHA-256、图层、组、蒙版、清单、对话和未确认草稿；打开时验证原照片没有变化。`skin_smoothing` 与其他配方字段共用滑杆、AI Schema、历史和保存协议，渲染时用保边平滑并由层蒙版限定范围；纯细节调整跳过颜色 LUT。

导出预检由 `session.exportImage` 在界面进程执行。`export_process` 为每次导出在目标目录创建独占暂存文件夹，`export_worker` 重新校验源照片摘要、按原图分辨率合成并只写暂存文件；主进程收到完整结果后再次检查目标，再用同卷原子发布，避免取消时留下半成品或覆盖外部新文件。预检成功只代表任务已排队；`Editor.exportCompleted(destination, error)` 才表示最终结果。`ExportDialog` 在此信号返回前保持打开，允许取消独立进程；失败或取消后保留路径可重试。Windows 在杀进程后可能短暂锁住临时文件，`export_process` 以 100ms 间隔重试清理，编辑不等待清理完成。主编辑 worker 继续处理预览，旧导出协议仅保留兼容入口。

项目源照片丢失时，`session.openProject` 保留已验证的项目载荷在 `_relink_pending` 并通知 `SourceRelinkDialog`。用户选择候选文件后，`_pending_project` 仅在后台 `open` 请求期间持有项目载荷，主 worker 核对 SHA-256 后才替换当前照片。校验失败时 `project_open_failed` 仍保留 `_relink_pending` 供重试；成功后的 `_opened` 恢复图层和对话，将照片路径变化记为未保存，保存项目会写入新路径。恢复副本也可走同一入口。不要在 UI 线程读取整张照片计算摘要，也不要在核对前修改当前文档。对话导出由 `ConversationExportDialog` 保留路径和行内错误；界面调用 `exportConversationAsync`，仅在 UI 线程取不可变字段快照，和项目/恢复共用串行写入器，完成信号决定是否重开失败面板。同步 `exportConversation` 供脚本调用。两者都用 `storage.atomic_output` 先写同目录暂存文件，再独占发布，避免同名覆盖和中途失败留下半成品。`ConversationPane` 使用 `conversationModel` 增量行模型；`_message` 追加、`applyAdvice` 刷新、`session._opened` 重置模型，`Editor.conversation` 列表仍用于项目序列化和旧接口。`conversationCount` 让按钮状态更新不必重新将整份对话传入 QML。

恢复副本只代表未保存编辑。`saveProject` 的原子写入成功后才清理当前恢复副本；从旧副本恢复时 `_recovered_from` 指向旧文件，只有新副本成功写入或项目成功保存后才清理它。恢复会话标为 dirty，并启动自动恢复计时器。`_discard_recovery` 只允许删除恢复目录中的 `.iphoto` 文件。启动时 `Editor.canRecover` 控制顶部“恢复未保存”入口；新副本接替旧副本后需发出 `changed`，让入口及时隐藏。写入失败时保留旧文件，避免清理先于持久化。

`Editor.conversationDraft` 是当前文档的未发送文字，`conversationDraftMode` 是它的操作模式。会话内按项目路径或照片路径分别缓存，保存为项目时迁移到项目路径；成功打开项目时，若会话中没有更新的草稿，则从项目的可选 `conversation_draft` 与 `conversation_draft_mode` 字段恢复。模式保存稳定标识 `edit/advice/regions`，旧项目缺省 `edit`；文字在 1.7 格式中限 4000 字，旧项目缺省为空。输入变化推进 `_edit_revision` 并标记 dirty，使显式保存和自动恢复使用同一份快照；已有草稿时切换模式也标记 dirty，空草稿时单纯切换模式仍视为导航。发送成功清空，失败保留。清空也写入会话缓存，避免旧文件的草稿在同一会话中重新出现。项目与恢复快照调用对话已有的 `_safe_text` 脱敏，避免把 `sk-` 形式的 Key 写入文件。

`AIController.requestProgress` 只报告传输层可观测阶段：请求提交后的等待秒数、收到字节后的接收状态，以及可重试网络错误的倒计时。独立的 `progressChanged` 每秒更新这两个轻量标签，不触发整个 `Editor.changed`。重试等待也算 `busy`，由可取消的成员 `QTimer` 持有，避免旧的 `singleShot` 在用户取消后再次发出请求。对话区的状态条与主窗口底部状态栏都绑定此属性，收起助手后仍能看见进度；非流式 JSON 响应无法提供模型内部推理步骤。

`masks.validate_bitmap` 对外部 PNG 完整解码一次以拒绝损坏文件，但只缓存经过校验的摘要，不保留展开后的像素。应用自身 `encode_bitmap` 生成的 PNG 可直接登记为已校验。真正渲染时 `_decode` 使用 64 MiB 字节上限的 LRU 保存展开图，而不是按固定图片张数缓存；项目中存在多张 24MP/60MP 蒙版时，这避免了校验阶段的内存累积。`document.RASTER_CACHE` 另缓存已应用笔画/羽化的栅格结果，同样有独立的 64 MiB 上限。

源照片成功切换后，UI `session._opened` 和主 worker 的 `open` 提交分支分别调用 `clear_decode_cache()`，释放旧照片的展开蒙版。失败的打开请求不触发清理，当前照片和缓存仍可继续使用；已校验摘要索引仅占小量内存，可以跨照片复用。细节图块进程在照片切换时由既有 `stop` 流程结束。

大项目恢复写入由 `Editor` 持有的单线程 `ThreadPoolExecutor` 执行，QTimer 在主线程取回结果。自动恢复只保留最新待写快照，写入器一次只处理一个文件；快照在主线程深拷贝，后台不得读取可变的图层状态。切图、关闭和后端同步 `saveProject` 先等待正在写入的恢复副本，再按原子写入流程保护当前文档。界面使用 `saveProjectAsync`：与恢复副本共用串行写入器，按钮显示“保存中…”，完成后才更新项目路径/dirty 并清理旧恢复副本。保存期间仍允许编辑；`_edit_revision` 区分保存快照与后续修改，后续修改保持 dirty 并重新触发自动恢复。保存期间暂不切换照片/项目；退出时等待保存结果，再确认恢复副本持久化。`projectSaveCompleted(path,error)` 报告文件实际写入结果，不能用请求入队成功代表保存成功。

QML 的另存入口是 `ProjectSaveDialog`。它预填 `suggestProjectPath()` 给出的未占用绝对路径，提交后关闭，让用户可继续编辑；后台 `projectSaveCompleted` 失败时自动以原路径和行内错误重开，直接 Ctrl+S 的保存失败也进入此面板。该面板的浏览器只负责选路径，项目实际写入仍必须通过 `saveProjectAsync`；不要把请求成功当作文件已写成。

全尺寸合成在 `engine.render` 对已是 RGB 的照片复用输入，只在 LUT 边界确有精度修正需求时才创建原像素数组；`document.render_nodes` 延迟复制输入画布，并在蒙版确为不透明全图且图层透明度为 100% 时直接接收调整结果。组、局部蒙版、修复和非满透明度仍走原复合路径。这减少大图导出的整图副本，同时保留原图像素级输出契约。

## 画布模块（1.4.1）

`Editor.viewport` 持有独立 `Viewport(QObject)`。成功加载照片时，worker bridge 把原图尺寸传给 `setSource`；QML 把窗口的 DIP 尺寸和屏幕 DPR 传给 `resize`。缩放比例 1.0 对应原图像素与物理屏幕像素一对一，图像在 QML 中的宽度为 `source_width × zoom / DPR`。全图始终使用最长边 1600px 的快速预览；当原图尺寸与缩放比例使代理像素明显放大时，`detail_tiles` 按可见矩形加边界缓存请求一块最多 800 万像素的原分辨率细节。`detail_worker.py` 独立加载源图，在带 128px 邻域的裁剪上合成调整层、蒙版与滤镜，再返回只包含目标矩形的 PNG；按照片代际与细节版本丢弃过期结果。`worker_bridge.preview_payload` 为代理和细节路径组装同一份草稿/分区预览状态，避免未确认调整在两种分辨率下不一致。显示复杂蒙版时，细节进程也生成相同原图区域的 RGBA 或灰度蒙版图块；QML 只在图块完全覆盖当前视口后用它替换代理蒙版，平移出图块时立即恢复代理蒙版，避免叠两层导致颜色加深。位图蒙版的源数据分辨率仍受上限约束。细节进程在最后一次请求完成后闲置 20 秒就退出，当前图块继续显示；下次移到图块外会重启进程。缩回适应画布、换图、导出或交互分割也会释放细节进程。

- `CanvasPane.qml`：画布上下文、百分比输入、适应/100%/导航按钮、帮助菜单与对话面板组合。
- `CanvasViewport.qml`：图像与蒙版定位、选区鼠标事件、比较分割线、导航手势路由。优先处理中键/空格/抓手/缩放，普通选区事件继续交给下层；不通过切换选区工具实现临时抓手。
- `CanvasOverlays.qml`：把对象轮廓和笔迹映射到屏幕坐标，纹理尺寸始终等于可见画布；不可重新放回大尺寸 photo Item 中分配 Canvas。
- `CanvasNavigator.qml`：缩略图与可视矩形、点击/拖动定位；复用已有预览，不生成新的图像。
- `viewport.py`：缩放锚点不漂移；窗口重排保持源图中心；平移保留可见边缘；窗口失焦/输入文字/打开弹窗时清理临时键与手势。只有本窗口事件会被过滤。

导航不进入项目或撤销，也不提交主编辑 worker 的合成任务；放大后静止约 180ms 会向独立细节进程提交最新可见区域，平移中的连续请求合并。选区点仍用相对于完整 photo 的归一化坐标；缩放、窗口重排、空格切换发生在笔画中途时，取消未提交笔画，避免把两个坐标系拼成错误选区。

`test_viewport.py` 覆盖几何与高 DPI；`test_canvas_ui.py` 使用真实 Qt 键鼠/滚轮事件，检查输入焦点、草稿不变、坐标对应、导航图、比较线和两种窗口尺寸。`scripts/benchmark_canvas.py` 单独测量软件渲染帧及同步读回时间，不能当作显示刷新率或真实 FPS。

## 图层组的确切语义

项目仍使用有界的平铺记录列表，每项有稳定 `id`、`kind`（adjustment/group）和 `parent_id`，从这些关系构造真实树。相同父节点的列表顺序是从下到上；界面反向显示。支持四级组嵌套，合计 32 项。拒绝循环父链、不存在的父节点、普通调整层作为父节点及重复 id。

组内各层先对进入该组的照片依次调整，再将组结果通过组蒙版和不透明度与进入组前的照片混合。组不透明度只应用一次。隐藏父组会隐藏所有后代；组蒙版外保持进入组前的像素。组没有直接调色参数，须在组内新建调整层。

这是调整图层组；尚未实现 PSD 导入、独立像素图层、智能对象、剪贴蒙版和 Photoshop 的所有混合模式。折叠仅改变显示，不影响合成；复制组会重新分配所有后代 id；删除组包含后代，整次动作可撤销。

## 添加一项能力

1.5 的像素后端入口是 `segmentation/service.segment`。当前 `EfficientSAM.predict` 接收 PIL 图像和原图尺度的坐标/标签，返回候选 logits、模型评分和计时；服务负责约束检查及位图编码。`models.py` 固定权重清单，`runtime.py` 处理 Windows C++ DLL 版本一致性；两者不触碰系统文件。`classical.py` 保存原 GrabCut/魔棒/U²-Net 实现，`plugins.py` 保留元数据和旧函数兼容出口。完整依据与边界见 [分割模块设计](../planning/iphoto-v1.5/selection-research.md)。

`test_pixel_selection.py` 测协议、提示约束和位图边界；`test_pixel_workflow.py` 测真实 Qt 输入路由及取消/过期/失败保护。旧协议事务测试显式使用 `pixel_protocol_stub`，不计入模型质量证据。`test_v14_ui.py` 和 `test_redesign.py` 的对应案例在权重存在时通过真实 worker 推理；首轮 CPU 编码使用 60 秒测试期限。自然照片对照用 `scripts/qa_pixel_selection.py`，几何真值测量用 `scripts/qa_segmentation_geometry.py`，二者均为真实模型。

照片预热由 `pixel_selections.warm` 启动 `warm_worker.py` 短进程，读取主 worker 生成的 1600px 代理 PNG，把 EfficientSAM 嵌入写入同一磁盘缓存。预热完成后，按需启动 `pixel_worker.py` 持久进程处理所有交互分割与元素轮廓预计算；主 worker 继续负责照片导入、预览和导出。交互点选优先于后台轮廓；若后台轮廓正在计算，结束像素进程并重排该对象，重启后先处理点选。取消正在执行的点选会结束像素进程，原选区不变；换照片会清理旧图的后台请求。结果按照片代际或元素清单版本校验。`scripts/qa_scene_preheat.py` 验证缓存真正从磁盘读取，`scripts/qa_cold_pixel_queue.py` 验证冷预热和执行中的取消，`scripts/qa_large_photo_priority.py` 验证后台轮廓期间的画框、换图与前台点选。

状态栏另用 `_status_epoch` 区分前台结果与后台进度。后台轮廓请求携带开始时的版本；前台请求或通知推进版本，过期的后台结果仍更新元素行缓存，却不能覆盖之后的导出路径、选区结果或错误提示。场景分析完成消息显式属于后台进度，因此无后续操作时仍可由轮廓完成数替换。

- **界面区域**：在 `ui/areas/` 增加组件，显式绑定依赖；仅将布局组合写进 Main/Inspector。
- **编辑动作**：在所属 controller 中实现，Editor 增加简短 Slot；通过 `_can_edit()`、`_commit()`、`_change()` 遵守草稿、历史和渲染规则。
- **本地选区后端**：在 `plugins.py` 添加 Capability 元数据以及 `SELECTION_BACKENDS[id]` 适配器。统一调用边界为 `(PIL.Image, validated_mask, options) → (validated_mask, quality)`。后端在 worker 运行，依赖延迟导入。记录代码/权重来源、许可证、文件校验和、CPU/GPU要求；模型下载应有独立安装入口。仅添加元数据而没有处理器会明确报错，不会误调其他模型。
- **AI 平台/协议**：平台地址与 Key 规则加入 `ai_settings.py`；请求差异进入 `ai_protocol.py`；结果解析必须有界、校验类型、完成状态、坐标/参数/id。模型只返回数据，不能提供本地可执行代码。
- **文档字段**：先定义迁移和验证规则，再补保存/恢复与正反例测试。1.7 可读取 0.1、1.2、1.3、1.4、1.5、1.6 项目；老版本不应覆盖新格式项目。

## 测试与调试

`test_v14.py` 覆盖对象协议/组合、图层组数学、非法层级、缓存一致性、项目恢复。`test_v14_ui.py` 用真实 Qt 鼠标键盘检查 1440×930 和 1080×700；截图标注 mock，因为照片对象来自测试清单。旧测试继续覆盖原图保护、导出、锁定参数、凭据、恢复失败、AI 中断和 UI 快捷键。

```powershell
# 离线；不读取真实云端 Key
.venv\Scripts\python.exe -m pytest -q

# 独立运行，避免与测试/导出争用 CPU
.venv\Scripts\python.exe scripts\benchmark_v14.py

# 仅在明确授权真实云端调用后运行；读取系统凭据，参数中不接受 Key
.venv\Scripts\python.exe scripts\qa_live_qianwen.py --live --stage workflow --mode workflow

# 真实 UI 全流程自测（选区/分区/修图/图层/对话），截图在 artifacts/live-selftest/
.venv\Scripts\python.exe scripts\selftest_live_ui.py --live
```

真实 API 验收只使用内置湖景，不上传用户私有照片。Windows 沙箱可能无法访问登录用户的凭据，应在同一用户的正常 Windows 会话执行。记录请求类型、延迟、HTTP/完成状态、经脱敏的结果与覆盖图；不记录 Authorization、Key 或完整图片请求体。`workflow_cached` 模式需先有 `artifacts/v14-live-workflow/real-workflow.iphoto`。`scripts/selftest_live_ui.py --live` 驱动真实 QML 走"文字选区 → 智能细化 → 输出两分支 → 分区草稿 → 图层操作 → AI 修图/建议"全流程并留截图；Repeater 委托控件用视觉树遍历查找（`QObject.findChild` 到不了委托对象）。

性能报告区分 CPU 合成和真实云端耗时；合成数值不包含 PNG、进程通信与界面显示。64 MiB 是缓存的逻辑像素预算，不是整个应用的内存上限。对象悬停使用 384×384 索引，不做逐帧分割或云端请求。


## 1.6 透明边缘模块

`matting/trimap.py` 管前景 / 背景 / 未知约束，顺序处理已知前景和背景的小孔洞连通域，避免同时保留两份整图标签；`matting/solver.py` 管有资源边界的 CPU 数值求解，服务调用时直接写入 uint8 透明度输出，浮点输出仍可用于数值验证；`matting/service.py` 管文档输入输出并在编码前释放不再需要的整图数组。`controllers/matting.py` 管草稿事务，`controllers/matte_process.py` 管独立一次性细化进程、请求代际和取消；`matting/worker.py` 读取原照片并验证 SHA 后调用服务。正常 UI 请求不占主编辑 worker，取消可直接结束细化进程并保留草稿；主 worker 的旧 `matte` 操作仍为兼容入口。`segmentation/` 继续负责对象语义与初选，两者不能混为一类模型分数。

新灰度 PNG 可带 `sampling: "alpha"`。`masks.py` 统一处理插值及零覆盖保护，`document.raster_mask` 统一处理手工修正和内羽化。当前项目 schema 1.7 接受旧格式；修改资产字段须同时检查验证、保存恢复、画布预览和原图尺寸导出。

“调色效果”预览由 `controllers/worker_bridge.preview_payload` 在深拷贝图层里替换草稿蒙版，供快速预览和原图细节共用。不要把临时预览写回真实层，否则会破坏撤销和取消。

真实求解、半透明边缘与小孔洞测试在 `tests/test_matting.py`。`scripts/qa_alpha_matting.py --natural` 生成已知 alpha 和真实山林对照；`scripts/qa_matting_ui.py` 通过真实 Qt 输入触发求解和预览，`scripts/qa_matting_cancel.py` 覆盖运行中取消、立刻重试、画框和换图。输出在 `artifacts/alpha-matting/`。范围、性能、未实现的颜色补偿见 [1.6 技术说明](../planning/iphoto-v1.6/alpha-matting.md)。


## 1.8 选区重构与新协议字段

选区交互的单一门面是 `controllers/selection_controller.py`（`editor.selection`）：工具、模式、笔刷、容差、蒙版显示与任务状态机都在其中，QML 只读绑定并经它下发；`draftBegan/draftEnded/toolChosen` 是视图联动的唯一信号源。共享组件在 `ui/components/selection/`（输出条、修边组、查看方式、任务条、工具选项条）；面板按"①做出选区 ②修边与检查 ③输出到图层"固定三区组织。分析与设计见 `doc/selection-analysis.md`、`doc/selection-redesign.md`，实施与验收见 `planning/iphoto-v1.8/`。

右侧检查器没有标签页：导航由 `SelectionController.pickedLayerId` 驱动——未选中图层时显示统一范围模块 `ui/areas/SelectionGuide.qml`（工具芯片、文字识别、元素清单行悬停=画布预览/点击=设为范围/＋−=叠加减去，走 `selection.rowSelect`；修边卡），选中图层时显示 `AdjustmentPane`（头部"返回范围"取消选中）。图层行点击=选中/再点取消；草稿开始自动取消选中；`apply/applyRegions` 落地后自动 pick 新层，"范围→调整"零切换。固定的 `DraftOutputBar` 发出 `refineRequested` 导航信号，由 `InspectorPane` 负责把 `SelectionGuide.refineCard` 滚入视口；子组件不直接修改滚动容器。元素清单与选区源同属一个模块，不再存在独立 ScenePane；旧经典面板与标签行已删除。

协议新增字段均向后兼容：mask 可选 `edge_shift`（int，-5~5，短边百分比，`raster_mask` 用 PIL Min/MaxFilter 收缩/扩张）；layer 可选 `inpaint:{method:"telea"|"ns",radius:1~25}` 与 `heal:{ops:[{kind:"heal",points,radius:0.003~0.2}]}`。两者都在 `render_nodes` 渲染时合成（先修复/填充，再套 recipe 与蒙版），因此非破坏、可撤销、可随项目保存；图层组不允许携带这两个字段。修改这些字段须同时检查 `validate_layers`、`preview_cache.node_key/layer_key`、`render_nodes` 与导出链路。

`worker.py` 的主循环是 op 注册表（`register("open"/"render"/...)`）；新增 worker 操作只需注册一个处理函数。worker 的 `open/render` 响应附 `stats`（通道均值与亮度 1/50/99 分位数，编码空间），供 `controllers/auto_adjust.py` 的本地自动调整使用——它是直方图规则，不是 AI。`document.RASTER_CACHE` 是蒙版栅格的 64 MiB LRU（与预览缓存预算各自独立），`render_nodes` 经 `raster_mask_cached` 复用；worker 在 `open` 时清空。蒙版字典按版本不可变是缓存正确性的前提：任何就地修改蒙版的代码都会造成脏命中。

真实 UI 全流程自测用 `scripts/selftest_live_ui.py --live`（读取 Windows 凭据中的千问配置，参数不接受 Key）；Repeater 委托控件（如 `parameter_exposure`）不在 `QObject.findChild` 可达范围，脚本用视觉树遍历查找。
