# iPhoto 源码仓库说明

本项目是 Python 3.12 + PySide6 / Qt Quick 的原生桌面照片编辑器。照片合成与本地分割运行在独立工作进程，云端 AI 返回经过校验的参数、对象位置或内部点。运行入口为 `run.py`，应用版本 1.9.14，项目格式为 1.9。

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
| `run.py` / `process_runtime.py` | 在重型库导入前选择进程角色、设置图片进程默认BLAS线程配置 | 在GUI中全局覆盖模型进程的环境 |
| `src/iphoto/app.py` | Qt 生命周期、字体、窗口加载、启动参数 | 修图规则和控件业务 |
| `workspace.py` | 唯一的 Editor QObject，Qt 属性、信号、槽和状态所有权 | 新的大段业务逻辑 |
| `controllers/worker_bridge.py` | 主工作进程队列、合并预览请求、结果代际校验、独立预热与像素进程生命周期 | 图像算法 |
| `controllers/preview_updates.py` | 连续参数预览节奏、可显示的中间帧代次范围 | 文档历史、图像算法、跨照片复用 |
| `controllers/detail_tiles.py` | 原图细节视口请求合并、裁剪范围、独立细节进程与过期结果隔离 | 图层算法 |
| `controllers/matte_process.py` / `matting/worker.py` | 原图透明边缘的独立进程、快速取消与结果代际隔离 | 主编辑 worker 的 UI 状态 |
| `controllers/export_process.py` / `export_worker.py` | 原图导出的独立一次性进程、真实阶段、取消、暂存清理与最终发布 | 主编辑预览队列 |
| `controllers/layers.py` | 图层、组、历史、手动参数、图层缩略图、当前显示提示与一次撤销的显示恢复 | 网络请求解析 |
| `layer_rows_model.py` | 图层侧栏的稳定 Qt 行模型，按变化行发更新 | 文档和图像处理 |
| `scene_rows_model.py` | 元素清单的稳定 Qt 行模型，同一清单只更新变化行，版本变化才重建 | 对象分割与文档修改 |
| `conversation_rows_model.py` | 对话侧栏的增量 Qt 行模型，追加消息只插入一行，建议状态只刷新对应行 | 对话序列化和 AI 调用 |
| `controllers/selections.py` | 独立选区草稿、笔画、修边、输出及分区确认 | 云端供应商配置 |
| `controllers/selection_controller.py` | 选区 UI 单一门面：工具/模式/笔刷/容差/蒙版显示状态、任务状态机、图层焦点、修边路由与输出分支 | 直接改蒙版数据（仍委托 selections 等模块） |
| `controllers/objects.py` | 元素清单、文字目标流程、点选和组合 | Qt 控件布局 |
| `controllers/pixel_selections.py` | 像素任务、提示点、对象缓存与草稿事务 | 分割算法、模型下载 |
| `controllers/adjustment_review.py` | 从现有局部层与祖先蒙版求有界查看位置，供100%效果导航 | 皮肤/部位识别、蒙版修改、云端请求 |
| `segmentation/` | 独立定位协议、点/框、ONNX 推理、候选筛选、边缘处理、传统算法 | Qt 控件、云端凭据、项目写入 |
| `segmentation/body_skin.py` | 身体部位的原图局部推理、原图坐标范围映射与多部位透明度合并 | 整体解剖分类、云端定位、图层事务 |
| `controllers/conversation.py` | AI 请求上下文、建议绑定、对话记录、结果应用 | 任意模型代码执行 |
| `controllers/session.py` | 项目打开、保存、恢复、关闭保护、导出 | 绘制控件 |
| `document.py` | 文档校验、蒙版光栅化、组与图层合成、项目序列化 | Qt 窗口或 API Key |
| `inpainting.py` | OpenCV修复/填充、二值范围、带上下文余量的局部求解、原尺寸合并及alpha保留 | Qt、历史、蒙版/图层写入 |
| `layer_tree.py` | 父组关系校验、层级视图、后代集合、子树复制、贯穿祖先的显示条件 | 图像 I/O |
| `scene.py` | 元素/目标协议、清单校验、集合运算、点选索引 | 网络和 Qt |
| `ai_tasks.py` | 轮廓与分区协议、规范化与严格验证 | 自动修改图层 |
| `ai_protocol.py` | AI 请求格式、缩略图编码、调色结果解析 | 凭据保存、网络生命周期 |
| `ai_layer_edits.py` | 修改已有图层的有界协议、目标 id、配方和显示/不透明度校验、保留参数锁 | 图层写入、Qt 状态 |
| `ai_layer_groups.py` | AI编组协议、相邻兄弟目标、容量和完整子树深度校验；元数据检查不复制蒙版 | 图层写入、Qt 状态 |
| `ai.py` / `ai_settings.py` | 异步 HTTPS；供应商设置与 Windows 凭据管理 | 日志输出 Key |
| `worker.py` | 有界 NDJSON 请求、图像加载与预览合成；保留旧导出和分割协议兼容入口 | GUI 访问 |
| `segmentation/warm_worker.py` | 按需在独立短进程编码代理图并落盘缓存 | Qt 状态、图层编辑 |
| `segmentation/pixel_worker.py` | 独立持久进程处理交互点选与低优先级元素轮廓；可结束并重启 | Qt 状态、图层编辑 |
| `detail_worker.py` | 按可见区域合成图层，成对交付带位置的效果和原图细节PNG | Qt 状态、画布导航 |
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

实际修复画笔经CanvasViewport→SelectionController.paintRepair(layer_id,originalUrl,generation,points,radius)→heal.paintRepair；按落笔时的照片、图层和文档代次绑定拒绝过期松手，并在busy/草稿/部位确认或缺OpenCV时保留文档。先验证笔画和容量，再选纯修复、可见、非零强度根层继续（无调色/填充，且范围为笔画或旧中性空/全图范围）；其他目标建立可见根层，60笔满时续层。保留自定义范围的已有层，不为复用清掉其范围；新修复层的缩略图范围按实际笔画同步。完整准备后提交此前手势、记录导航、一次修改/历史/代次并选中结果，第一笔与建层同一步，busy先拒绝避免错误通知消费AI请求。旧Editor.drawHeal是明确向当前层追加的文档原语，旧混合层的合成与蒙版/笔画语义保留，不用它路由实际画笔。

activeRepairInfo.isolated仅是从当前recipe/inpaint计算的显示能力，不序列化；纯修复层有专用强度滑杆和固定查看/继续入口，混合旧层仍显示图层整体强度和调色范围操作。repairFocusRequested只在实际picked层切换或从范围明确选中修复层时发，_on_editor_changed跟随活动层也必须发，不能在自动同步picked之后只依赖pickLayer差值。AdjustmentPane经既有Inspector布局完成后的有界定位将修复控件完整移入视口；后续笔画、强度和渲染changed不重复滚动。新画笔/选中/AI修复均沿同一选择门面，固定查看复用逐笔原像素导航，不增加文档步骤。

SelectionController.reviewedRepair记录明确查看后的当前笔画，暴露本层1起始编号/笔数、稳定layer_id和一次查看的临时token，不进入工程。查看快照复用原导航key中的ops，按照片SHA、当前层和picked层核对；笔画内容比较按token+documentGeneration缓存，普通渲染/状态通知不重新扫描或光栅化，强度/名称/调色变化允许继续删除同一笔，笔画变更则拒绝。同一AI多层结果使用本层编号，不将全局循环位置当作本层索引；重复点位不靠list.index猜测。按钮按下时捕获token，松手经Main.commitPendingText和SelectionController.deleteReviewedRepair再次检查，重新定位产生的新token不能消费旧按压。busy/草稿/分区/过期对象不改文档或消费AI请求，换照片/重开项目清空查看引用。

heal.removeStroke在当前层及完整ops快照吻合后准备修改：只删目标笔画；无调色/填充且蒙版精确等于笔画范围的层同步剩余缩略图范围，混合和自定义范围保留原蒙版。最后一笔若仅剩空的笔画范围层，且还有其他调整层，删除此层（按钮提前显示“删除修复层”）；否则去掉heal字段并保留层，唯一调整层仍存在。候选校验完成后结束前一个参数手势、记录导航并一次提交/代次，删除不要求OpenCV可用。保存中的旧快照与当前删除按原edit_revision隔离；撤销/重做恢复全部原层、笔画、mask、锁和强度。成功后清空查看引用/循环，防止重复点击接着删下一笔；AI回复按实时ID过滤已删除结果，仍可继续查看剩余结果。UI纯修复层隐藏下方重复的不透明度滑杆；混合层固定栏分两行放置调色范围和修复操作，修复行在下方，所有按钮仍在滚动区外。

参数数值输入由`ParameterValueField`持有临时文字和首次编辑时的layer_id/originalUrl/documentGeneration；不写入文档、恢复或历史，也不逐字渲染。回车、普通失焦或保存/导出时经`SelectionController.applyParameterText`再次核对当前层、照片、代次和编辑权限，然后严格校验长度、十进制、有限范围与整数要求，沿现有setParameter/finishGesture提交一次、保留手动锁与撤销语义。无效输入不夹取、不改文档状态，普通失焦保留文字与错误，避免输出动作取焦点时悄悄丢弃；换作用域、后续编辑或忙碌仍丢弃旧文字。保存、另存和导出统一经InspectorPane/AdjustmentPane的commitText布尔接口检查全部尚未提交的字段（不只activeFocus），调用submit(true)在失败时回到错误字段；普通失焦submit(false)不强抢焦点，Esc明确撤回。Main.exportPhoto统一按钮/菜单/Ctrl+E入口；不要绕过门面直接把QML文字写入recipe，也不要通过禁用Tab焦点改变按钮键盘能力。数值框明确上下padding，短窗口仍核对字段文字高度与控件/滑杆可见范围。

原像素对比与效果图块来自同一次detail请求：worker从已核对摘要、方向和色彩的Source.image裁出相同box的原图PNG，沿现有RGB/RGBA和ICC保存，不从1600px代理重建。当前裁片以(source_sha,box)复用；重复调参数将原图文件移到临时资产列表末尾，继续最多8个文件的预算，避免当前原图先被清理。主进程只沿原current/intermediate及源SHA条件接受成对结果，detailOriginalUrl随stop/换图清空、随park保留。ViewportDetailImage的sourceFrame只放宽文档代次/版本条件，照片身份和已显示矩形仍严格检查；原图本身不随配方改变。原图覆盖层在自身纹理就绪且覆盖视口后显示，半屏仍由原比较裁切容器限制，不重新请求效果、不改文档或分割位置。异步原图保持自身上一帧坐标，未覆盖当前视口时显示代理和加载说明。worker源缓存同时检查路径和摘要，同路径换内容也必须重新读取/核对。

`Editor` 持有一份文档状态；`controllers` 是按功能组织的动作函数，首个 `self` 参数由 Editor 显式传入。Qt 槽保留在 Editor 中，函数委托到对应模块。这样 QML 与测试入口稳定，同时避免 PySide 多层 QObject 继承与重名 Property 引发的元对象冲突。选区交互状态（工具、模式、笔刷、容差、蒙版显示、任务种类）是例外：由 `SelectionController`（`editor.selection`）这个单独的 QObject 持有，QML 只读绑定并经它下发，避免窗口级属性与面板各自为政；`draftBegan/draftEnded/toolChosen` 是视图联动的唯一信号来源。

图层编辑目标 `_selected` 属于文档；`SelectionController.pickedLayerId` 的空值表示用户正在看范围面板。焦点非空时随文档当前层同步，删除、移动、撤销和重做通过 `followActiveLayer()` 揭示当前层；它保留用户明确选择的范围模式。新建全图层、复制和编组主动 `pickLayer`。`layerFocusRequested(id)` 表示一次明确的定位意图，能处理层id不变但位置或父组改变的情况。`LayersPane` 在列表布局稳定后定位；参数、渲染进度等普通 `changed` 不重复滚动。新文档 `_opened` 在发布图层前重置旧焦点和草稿导航状态，避免恢复时意外展开文件保存的组。

范围草稿的用途属于文档状态：`beginSelection("current")` 将 `_selection_target_id` 绑定到当前层，新范围则为空。1.8 项目及恢复副本将其保存为 `selection_target_id`；校验要求目标是当前层且存在选区草稿。`SelectionController.applyDefault()` 按此用途保存原层蒙版或建立新层，QML 只显示对应的主按钮。完成或取消清空绑定；修正结束后恢复原层参数面板。旧项目缺省为空，沿用新范围行为，不能根据蒙版相似度猜测用户用途。修边、笔画和异步选区结果只能替换草稿内容，不能丢失原层绑定。

新的状态字段在 Editor 构造时初始化，源图片切换时由 `session._opened` 重置；需要恢复的字段同时补入 `session._payload` 和 `document.validate_project`。只产生通知的导航动作不要增加照片编辑历史。现阶段控制器共享 Editor 状态，后续继续拆分时可以引入专门的文档状态对象，但不要复制多份互相不同步的图层列表。

QML 区域必须显式接收 `workspace`（界面状态）和 `editor`（业务接口）。禁止跨文件引用别的区域内部 id；用 `focusPrompt()`、`commitText()` 等小接口连接区域。测试需要稳定的 `objectName`。模型生成文本必须使用 PlainText 显示。

步骤指导通知经_notify(..., scope="draft")发布，仅在selection或region草稿仍存在时接受；已经被同步自动输出消耗的草稿不能再发布初始指导，拒绝时状态、status_epoch和通知信号都不改变。Editor.notificationScope只是最近一次通知的临时元数据，QML在既有notification(message,error)到达时保存快照，不持有文档/任务状态，也不进入项目或历史。错误强制使用空scope，普通成功/保存等通知默认空scope。Main的toast在draftEnded、resultApplied或imageOpened时只结束draft指导，其他通知仍按4400ms计时；新的完成/取消通知正常显示。局部层、填充及分区输出明确发布当前操作结果，避免底部status仍指挥已结束的步骤。不能按提示文本猜测生命周期，也不能在每次editor.changed时清空通知。tests/test_notice_lifecycle.py覆盖完整Qt输出/修正保存与取消、独立成功和错误晚到事件、同步自动输出的初始指导竞争及分区/填充状态。

图层行由 `layer_tree.display_rows` 生成轻量摘要，`layer_rows_model.LayerRowsModel` 作为 QML 的稳定 `QAbstractListModel`：行顺序/id 不变时只发送变动行的 `dataChanged`，结构变化才重置列表。`Editor.layers` 的列表属性保留给旧接口。`maskToken` 取蒙版字典身份：正式蒙版的编辑、项目恢复与撤销均替换字典，因此即使标签不变，也会触发 `layersChanged`；普通状态或透明度变化不会重取缩略图。`LayersPane` 对已显示的蒙版版本做本地比较，只有版本变化或新行进入视口时读取缩略图。若以后引入原地修改正式蒙版，必须同时改为显式蒙版版本号或确保该修改能推进 token。

右键菜单调用 `layerContext(id)` 获取实际目标的操作能力和合法组列表，不读取当前选中层来判断。查询只校验层级元数据，过滤自身、后代、当前父组、超深移动及没有变化的根位置；上/下边界、32项限制、最后一个调整层和忙碌/草稿状态统一判断。`runLayerAction(id, action, parent)` 执行前再次校验，非法或失效目标不改变焦点、历史或渲染代次。合法命令先结束此前手势，再将命令前导航记录到已有历史帧；直接选择目标并应用动作，免去 `selectLayer` 的独立预览请求。隐藏/删除其他目标保留当前层，当前层被删除才选替代层。QML执行前 `dismiss()` 整个菜单链，避免绑定更新销毁被点击的组菜单项，使旧菜单无法关闭；菜单打开只读目标信息，关闭之后再修改文档。

## 三条主要数据流

1. **文字与对象选择**：`SelectionGuide → selectByDescription → objects → pixel_selections → pixel_worker → segmentation/service`。没有清单时调用 scene 返回框/内部点；随后 targets 只发送文字和对象 id。每个对象先生成像素蒙版；已有像素结果复用缓存。批量勾选的主操作带 `auto_apply` 意图，结果返回后一次性建立局部调整层并切到参数面板，失败时保留原图层；手动组合仍生成可修边的草稿。S 点选跳过云端。清单缺少目标时可“直接识别此目标”。
   单对象replace直接复制已有蒙版，不在界面线程放大到原图并重压PNG，也保留原有连续透明度、零覆盖区及存储分辨率。多对象、排除及加减交集由同一个pixel_worker在生成缺失像素范围后执行`segmentation/object_composition.compose`；请求包含原图尺寸、目标id、已有像素缓存、原范围和模式快照。先并目标，再减排除对象，最后与原草稿组合，与既有alpha语义保持一致，输出原图尺寸PNG；前端不再重组。全部目标必须有真实位图，重复/缺失/粗框/超限请求在分配组合画布前拒绝，前端再校验结果类型、尺寸和非空范围。结果由照片代次、源摘要和元素清单版本约束；取消复用像素进程中止和全局任务栏，原图层与范围保留。只组合缓存无需模型或嵌入，不能把它误标成照片已编码；照片编码中也不阻塞缓存组合。新分割+组合只派发一次工作请求，完成才应用一次事务。
   前台小目标的弱结果经 `controllers/object_grounding` 自动重定位：AI已配置、粗范围bbox面积不超过全图10%、有限数值predicted_iou低于0.85且没有detail_grounded标记时，缓存对象点击和批量调整会直接进入；首次对象、文字selection和自动regions在像素结果返回后检查。背景precache及显式对象组合的local_only模式不触发额外云端请求。`ai_grounding.object_crop` 只栅格到384px以计算边界，主worker的object_crop从已解码、已定向的原图裁切，最长边缩到1280并保留ICC，UI不解码整图。局部selection协议的真实内部点经map_grounding按实际源裁片边界映射回整图，再交给原pixel_worker；不把云端框直接当成最终蒙版。
   重定位持有源SHA、清单版本、文档绑定、范围/target_id与完整图层快照（忽略纯导航折叠）；每个裁片有独立token。全部局部坐标完成才重跑需修正的像素任务，保留已取得的其他目标位图与原配方；组合仍使用原草稿快照，不将旧弱缓存混入新结果。最终必须收到每个修正目标的实际像素结果，标记detail_grounded防止自动循环。准备与云端阶段共用固定任务栏及取消，过期/取消/unsupported/HTTP错误/缺失结果不消费范围或创建部分层；失败保留请求来源以记录到对话。自动分层继续一次事务、一步撤销。质量警告仍传递，detail_grounded只表示执行过局部定位，不代表人工真值准确率。
   为减少结果交付停顿，前端仅对即将绘制的组合结果使用`validate_mask(cache_bitmap=True)`，将已验证的解码置入既有64MiB缓存，避免立刻重解码；普通项目验证仍不保留解码像素。非空512px检查和覆盖率256px统计复用另一个64MiB栅格缓存。连续alpha缩放的零覆盖支持在最近邻缩放后才二值化，和先在原图二值化再最近邻缩放逐像素相同，避免每次缩略图创建原图大小的支持图。不能通过把输出蒙版降为代理尺寸提高组合速度。
   自动对象与AI自动分区经 `layers.addLocalLayers` 直接应用，手动范围与分区预览继续使用草稿。事务在修改文档前验证完整层组、容量、可见根层及非空范围；先提交此前未结束的参数手势，再一次追加整批、一次历史与一次编辑代次。自动对象可在成功时替换当前范围草稿，失败时保留；旧范围消息刷新为已取消。新层取目标名称、放在根级，避免继承隐藏、零强度或限制范围的父组。`SelectionController.showAppliedResult/resultApplied` 明确通知查看结果并退出原图比较，不借临时draftBegan/draftEnded改变界面。最终助手消息直接为applied，缺失/无效自动结果的失败也保存进对话。
   应用后的参数导航使用独立 `SelectionController.focusChangedParameters(previous)` 和 `parameterFocusRequested(layer_id,key)`，不订阅连续changed来反复滚动。新局部/全图层与范围调整沿真实最终配方比较零基线；已有层、绑定范围和应用建议比较实际修改前配方，锁定未改值不产生导航。优先暴露本次改变的磨皮/细节/色彩控件，其次明暗；组、草稿、隐藏/零不透明度效果及无参数变化不派发。先pick对应层，再由AdjustmentPane下一次事件核对layer_id和草稿状态，复用revealParameter展开单个分组及InspectorPane的frameSwapped定位。其他分组折叠状态保留，后续渲染/手动滑杆不重复请求定位；排队后返回范围或换层时丢弃旧定位，不增加文档/渲染/撤销步骤。
   局部结果查看复用同一选择门面和参数导航。`controllers.adjustment_review` 只从当前有效ID读取有参数的局部调整层；修复层使用既有逐笔查看。能力查询只检查元数据，不在changed回调光栅化大位图。用户点击后以最长边512px的缓存范围与全部祖先蒙版相乘，找连通范围中较大的最多四块（忽略不足最大块1%的散点）；每块距离变换最深的点供100%查看，避免落在眼唇/手机孔洞或分开范围之间。此算法只提供导航位置，不识别身体部位，也不修改蒙版。NumPy坐标在QObject边界前转成Python float，保证visibleRect仍为QML可比较的数字。游标仅在门面中，源图、mask身份或有效范围改变时重置；隐藏/强度沿原显示条件提示，不恢复原状态。首次查看或切换层复用focusChangedParameters，连续同层查看保留参数滚动。对话保存可选adjustment_layer_ids（1～4个唯一、非空、最长64字符ID），自动建层和已有层修改由程序给出真实ID；旧已应用回复仅沿原layer_id查看仍存在的局部层。删除/撤销后隐藏入口，重做/重开可重新使用，不按名称猜测替代层。忙碌/草稿/无范围时保持文档与导航；新入口不请求云端或重新分割，原像素仍由现有detail worker加载。
   `segmentation.grounding.COORDINATE_PROMPT` 统一对象定位、分区与自动规划的坐标约定：当前输入图片的左上[0,0]、右下[999,999]，x向右、y向下，不允许负数或中心原点；局部放大图也相对于当前图换算，不减原图裁切偏移。解析仍严格校验数值、框顺序与内部点，不取绝对值或猜测修复坐标。修复小点协议另有局部允许区与半径约束。
   首次画面分析的 `scene.SCENE_PROMPT/SCENE_SCHEMA` 使用相同坐标网格，单独明确 `box={left,top,right,bottom}`、`point={x,y}`；名称、类别与说明仍中文，同类采用相同类别。具名结构在边界逐项检查完整键、额外键、有限数字与0～999范围，再由`_scene_box_hint`转换成原box_hint的数组检查框顺序及内部点。新具名格式不接受负数/边界哨兵；旧数组和多边形仍按既有兼容协议读取。SceneIndex内仍是原0～1蒙版与anchor，项目/缓存格式不变，不再向渲染或像素工作进程传具名坐标。整份清单解析成功才发布，非法项不会建立部分清单；既有一次自动校正、固定等待/取消、失败保留旧清单及勾选沿原路径。三张用户大图的实际生产首次请求均通过，但不能据此保证其他照片/供应商不再需要校正。
2. **智能修图对话**：默认 `auto` 协议用严格字段的 `action` 决定调整当前图层、建立全图层、自动建立1～4个局部层、只回答或说明不支持。`scope` 明确动作对应当前层、全图、分区或无修改；`parse_auto` 复用配方与分区验证，并拒绝动作与范围冲突。局部结果经 `pixel_selections.select_regions(auto_apply=True)` 送入独立像素工作进程，全部蒙版完成后才原子创建图层并一次提交历史。Qwen 的 JSON Object 响应可能给出数组或越界参数；提示包含完整参数范围，客户端校验失败时将具体原因加入系统提示并仅重试一次，重试期间维持忙碌状态，仍失败则不改照片。画面分析 `scene` 和分区规划共用此修正路径。模型若沿用旧协议要求用户先手选磨皮范围，对话可再调用现有 `regions` 规划并沿同一自动建层路径执行。明确选择“分区预览”时仍保留检查草稿和手动确认。
   已有单个范围草稿时，智能修图直接启用，`current_scope=selection` 优先于所选层/组范围。请求附当前真实蒙版图、`selection_output` 与绑定层id；只接受 `adjust + current_selection` 或不改图的 answer/unsupported，禁止暗中扩大到当前层/全图、重新分割、修改其他层或编组，旧式单层已应用回复需重试补全范围。新草稿使用新层零配方与空锁定，不继承所选层的参数/隐藏父组；绑定层修边使用该层自己的配方和锁定。请求保存精确mask、target_id和完整图层快照（复用既有layer_snapshot）；`layers.applySelectionRecipe` 应用前检查代次、绑定、快照、非空范围、配方、锁定、可见性与容量，全部验证后一次保存蒙版和调整。新选择经addLocalLayers建立独立可见根层；已有层只更新原层，组内已有层的验证包含完整层级，容量满时仍可修改已有层。成功消费草稿并将旧draft消息标confirmed，使用showAppliedResult定位结果；取消、错误、无效果或过期保留草稿及其撤销历史。这个模式不触发旧的磨皮分区回退，也不重新调用像素模型。只给建议/分区预览不消费范围；草稿期间界面指引切换智能修图，离线本地规则继续使用手动开始调整路径。
   `current_scope` 只将没有位图/笔画的显式全选蒙版、且位于最外层的调整层视为 `whole_image`；不能通过标签或缩小采样推断全图，以免漏掉微小保护区域，组内层也不能假定覆盖全图。局部层不接受 `adjust + whole_image`，旧单层已应用回复在局部层上也需重试补全动作与范围。`global + whole_image` 使用严格范围的新配方、不继承当前局部锁定、不允许全图磨皮，经 `layers.addGlobalLayer(recipe, at_root=True)` 一次创建带配方的全图层并提交历史；无需像素分割。全图层在最外层，已有局部层完全保留，撤销不会先留下空白全图层。容量不足直接拒绝创建。
   显示条件与范围分开：`layer_tree.display_states` 从全部祖先的显示标志与不透明度计算 enabled、累积 opacity 和阻挡层 id，不受组折叠影响，也不把它当作蒙版覆盖证明。请求增加 `current_display_enabled` 和各已有层的 display 状态。当前显示条件不成立时，`parse_auto` 拒绝 `adjust` 与旧式已应用回复，反馈要求使用可见全图动作，或明确用 `update_layers` 更新已有参数。整图请求保留先前隐藏状态，不推测恢复原隐藏效果。`conversation._layer_result_notes` 对任何目标参数修改报告自身/父组隐藏或零不透明度，替换可能误报视觉成功的模型摘要；显式当前层编辑、本地规则和应用建议共用这些提示，隐藏层参数仍可有意更新与撤销。
   自动建层方案中含磨皮的区域会先经过 `ai_grounding.region_crop → selection`：从定向代理图裁切并留出 25% 余量，逐部位请求精定位。`map_grounding` 使用实际裁切像素边界将局部坐标映射回整张照片，保留原配方。原始请求一直保存在 `_pending_request`；全部精定位完成后才进入像素分割，取消或任一部位无法定位时不创建部分图层。每个独立磨皮部位增加一次请求，进度条显示具体部位。
   分区 `mask_target` 区分 `object/face_skin/body_skin`。面部专用模型保留初始 `skin_crop` 上下文，避免精定位窄框遗漏五官信息；普通对象继续代理图分割。身体区域可含最多四个严格 `parts={box,point}`，每项必须位于整体框内、内部点必须落在自身框内；无部位列表时按整体框与点处理。其他类型不接受部位列表，缺少框和内部点的身体方案在解析阶段拒绝。`conversation` 将身体列表展开成有界精定位队列，逐项保存初始上下文与重新定位结果；前端仍只有一个区域和一个调整层。
   `pixel_worker` 对面部/身体任务一次读取并核对原图摘要、方向和色彩；身体适配分别裁出原图上下文、缩至最长边1600px，转换点位并裁切整图提示范围后调用既有 EfficientSAM。各局部alpha恢复到对应原图位置，零支持区保持零，使用max合并而不重复叠加效果；整组完成才返回一个原尺寸位图。`body_skin_available` 由现有SAM能力给出，身体任务停止无用的全图编码预热，各局部沿既有磁盘缓存复用编码，不能把局部编码误标为全图就绪。分割前发NDJSON进度；`worker_bridge` 只接收同请求/同代次、非取消的前台身体作业和1≤part≤total≤4的整数进度，进度只更新状态，不释放作业或发布部分图层。任一部位错误仍沿原子事务保留已有层，取消结束像素进程。
   `update_layers + existing_layers` 专门修改已有层：请求提供稳定 id、名称、层级、范围标签、配方与锁定项，模型返回 1～4 个 `layer_edits`。严格 Schema 的每项包含目标 id、recipe（完整配方或 null）、visible（bool 或 null）、opacity（0～1 或 null）；null 保持目标原值。JSON Object 供应商省略未修改项时也可保留原值，纯显示操作不必伪造配方；全空操作仍拒绝。`ai_layer_edits` 拒绝未提供/重复目标、非法显示/不透明度、越界或不完整配方；组目标只允许显示与整体不透明度，recipe必须为null，保留目标层自己的锁定值。顶层配方必须保持当前值，分区必须为空，其他动作不能混入修改项。请求保存文档绑定与图层快照；应用前 `layers.applyLayerEdits` 再校验快照、全部目标、参数和显示控制，然后一次写入并提交历史。不会重复建层或再次分割；无变化不新增撤销步骤，锁定项与显示状态由程序说明。当前层不在目标中时自动选中第一个修改层。`SelectionController.pickLayer` 展开选中层的全部祖先组，只发模型与导航更新，不增加渲染或撤销步骤；父组隐藏/不透明度零时明确说明该层效果仍不可见，不擅自修改父组。
   `group + existing_layers` 使用独立的 nullable `group` 字段（name、layer_ids、visible、opacity），其他动作不得夹带编组；JSON Object 旧回复可省略无用的group字段。`ai_layer_groups.validate_group_plan` 只允许同一父组下相邻的已有兄弟项，按原清单顺序规范目标id；父组/子组不会因扁平记录中的后代穿插而误判相邻。检查新组占用32项容量和全部子树的四级深度，不以子层各改强度代替组整体强度。`layers.applyGroupPlan` 应用前再次检查整个文档快照的内容与顺序，忽略纯导航折叠状态；只浅复制改变parent_id的记录，保留大位图、配方、锁、修复数据和显示控制。新组使用全图蒙版、零配方，插入原兄弟块位置；未减弱的组与原合成像素一致。新组整体opacity在子层合成后生效。一次提交历史、一次预览更新，统一pick结果组；已有组可继续通过update_layers控制显示与整体强度，即使没有剩余图层容量。合法AI图层修改在命令前记录当前导航，撤销恢复原查看层；取消、无变化和校验失败不增加撤销步骤。回复由程序生成真实目标、整体强度和不可见原因，不使用模型的完成声明。
   `repair + regions` 使用独立 `repairs` 数组（1～3个name/reason/box），不改顶层配方，也不得夹带调色分区、已有层修改或编组。请求声明本地修复能力与图层容量。`ai_repair.repair_context` 为部位留出20%余量，按源图尺寸计算裁切、允许区及笔画半径范围。`repair_crop` 由主图像工作进程直接裁切已解码、已定向的源图，不从1600px预览图放大小点，不在UI线程重读完整源文件；因此也支持已解码的RAW。返回尺寸与当前文档核对后，内部 `repair` 请求只发送这一张局部图和局部坐标规则，已裁切PNG不重复裁切。
   点位协议1～8个point/radius/reason，坐标严格0～999，以局部左上为零；校验允许区、有限半径、重叠与局部总面积≤3%。`map_repair_spots` 使用实际源图裁切边界映射，笔画为估计半径增加50%及2个源像素边缘余量，仍受原图短边1%与局部半径40/999上限限制；对实际扩展圆再次校验允许区、重叠和面积，避免留下有色边缘或超出用户部位。独立修复层使用零配方及与笔画一致的向量范围，便于缩略图、后续范围与强度控制；图层处于最外层，避免父组隐藏/裁切。`heal.applyAIRepairs` 在一次历史提交之前验证全体部位和完整图层快照（含名称、锁、修复数据与顺序，忽略折叠导航），拒绝跨部位重复点。所有精定位结束后一次建层、一次编辑代次；无法识别的部位保持不变并列明，错误、过期或取消不留下部分修复。准备原图阶段也有忙碌状态和取消，晚到的裁切结果按请求token丢弃；接口校验错误最多重试一次。实际笔画数量及图层名由程序生成，不使用模型的完成声明。`MIN_STROKE_RADIUS` 在文档、修复工具和AI映射共享，避免工具允许小笔画而保存/工作进程拒绝。
   修复结果可经对话与调整面板调用`SelectionController.reviewRepairs(ids)`，只按仍存在的稳定图层ID读取当前heal笔画；不按名称猜测已删除目标。新修复回复保存可选`repair_layer_ids`（1～3个独立、非空、有长度限制的ID），工程读取保留，旧工程无需此字段；原系统生成的旧修复回复可沿原末层ID查看。全部部位按实际笔画顺序循环，游标仅属于选择控制器；源图/ID/笔画变化后重置，图层强度变化不丢失位置。`Viewport.focusRegion`按源图区域、画布大小和DPR保留上下文，小点最多100%、长笔画缩至适应；不在UI线程解码照片，也不新增修复、编辑历史或云端请求。跨层选择/展开组沿原导航生命周期。查看关闭蒙版覆盖和原图分屏，隐藏或零强度效果继续保留并说明；`activeRepairInfo`分别给出本层与祖先强度，不能把图层强度当作算法置信度。草稿/分区预览/忙碌时禁用；撤销/删除后入口按实际存在的笔画更新，不复用旧坐标。
   对话失败恢复：auto/edit/advice/regions发送时保存可选request_binding，新错误以request_user_id引用最初用户消息，多阶段皮肤/修复/对象定位沿原pending保留引用。`failedPromptInfo`只接受已失败错误和真实先前用户消息，核对现代引用的模式/绑定；旧项目只恢复紧邻、同模式/层ID的用户要求，不跨回复猜测。用户文字已沿原安全文本路径保存；新可选字段分别验证SHA64和引用长度。`retryFailedPrompt`核对当前照片/选中层/全部图层内容与草稿目标，排除纯折叠导航；复用已有文档绑定，不再次序列化大图层位图，补充名称/锁定/修复/填充和草稿。核对只在发送/重试时执行，错误行刷新不重算大蒙版摘要。变更时只恢复供检查，忙碌、分区、已有新输入或AI未连接时不发起重试。重试沿普通sendMessage使用原模式和当前保存连接，不自动切换供应商；原校正和网络退避策略继续生效。取回恢复文字与建议/分区模式；已有不同输入时复制原文字到剪贴板并保留输入。恢复只改变对话草稿，不增加图层/编辑历史；HTTP400/401/403/404/429错误提供设置入口，只打开，不修改配置。当前恢复面向对话四种模式，scene/selection/targets错误沿原范围操作继续处理。
3. **编辑预览**：Qt 槽 → 对应 controller → 状态/历史 → 90 ms 合并计时器 → worker NDJSON → 校验图层 → 合成缓存 → PNG → QML Image。连续参数输入保持已有计时截止时间，定期提交最新快照；松手finishGesture立即提交尚未派发的最终快照。参数每次修改仍推进文档代次、锁定和dirty，AI等异步动作继续使用严格代次校验。preview_updates只为连续参数变化记录[first,last]范围；没有插入其他文档变化且last仍等于当前代次时，可显示该范围内已经完成、不会倒退的颜色帧。任何非参数_change清空范围；直接推进代次的选区/切图/选层也因last不匹配而失效，下次参数不能重新打开旧范围。中间帧保留真实代次，既不覆盖当前参数也不表示最新效果Ready，过期蒙版不发布。换图、撤销、重做、重置和结构变化沿用严格隔离。
4. **输出**：手动草稿点“开始调整此范围”后创建局部层；批量对象选择从主操作直接创建。随后用新文件名导出。保存项目保留源路径、SHA-256、图层、组、蒙版、清单、对话和未确认草稿；打开时验证原照片没有变化。`skin_smoothing` 与其他配方字段共用滑杆、AI Schema、历史和保存协议，渲染时用保边平滑并由层蒙版限定范围；纯细节调整跳过颜色 LUT。

导出预检由 `session.exportImage` 在界面进程执行。`export_process` 为每次导出在目标目录创建独占暂存文件夹，`export_worker` 重新校验源照片摘要、按原图分辨率合成并只写暂存文件；主进程收到完整结果后再次检查目标，再用同卷原子发布，避免取消时留下半成品或覆盖外部新文件。预检成功只代表任务已排队；`Editor.exportCompleted(destination, error)` 才表示最终结果。`ExportDialog` 在此信号返回前保持打开，允许取消独立进程；失败或取消后保留路径可重试。Windows 在杀进程后可能短暂锁住临时文件，`export_process` 以 100ms 间隔重试清理，编辑不等待清理完成。主编辑 worker 继续处理预览，旧导出协议仅保留兼容入口。

项目源照片丢失时，`session.openProject` 保留已验证的项目载荷在 `_relink_pending` 并通知 `SourceRelinkDialog`。用户选择候选文件后，`_pending_project` 仅在后台 `open` 请求期间持有项目载荷，主 worker 核对 SHA-256 后才替换当前照片。校验失败时 `project_open_failed` 仍保留 `_relink_pending` 供重试；成功后的 `_opened` 恢复图层和对话，将照片路径变化记为未保存，保存项目会写入新路径。恢复副本也可走同一入口。不要在 UI 线程读取整张照片计算摘要，也不要在核对前修改当前文档。对话导出由 `ConversationExportDialog` 保留路径和行内错误；界面调用 `exportConversationAsync`，仅在 UI 线程取不可变字段快照，和项目/恢复共用串行写入器，完成信号决定是否重开失败面板。同步 `exportConversation` 供脚本调用。两者都用 `storage.atomic_output` 先写同目录暂存文件，再独占发布，避免同名覆盖和中途失败留下半成品。`ConversationPane` 使用 `conversationModel` 增量行模型；`_message` 追加、`applyAdvice` 刷新、`session._opened` 重置模型，`Editor.conversation` 列表仍用于项目序列化和旧接口。`conversationCount` 让按钮状态更新不必重新将整份对话传入 QML。

恢复副本只代表未保存编辑。`saveProject` 的原子写入成功后才清理当前恢复副本；从旧副本恢复时 `_recovered_from` 指向旧文件，只有新副本成功写入或项目成功保存后才清理它。恢复会话标为 dirty，并启动自动恢复计时器。`_discard_recovery` 只允许删除恢复目录中的 `.iphoto` 文件。启动时 `Editor.canRecover` 控制顶部“恢复未保存”入口；新副本接替旧副本后需发出 `changed`，让入口及时隐藏。写入失败时保留旧文件，避免清理先于持久化。

`Editor.conversationDraft` 是当前文档的未发送文字，`conversationDraftMode` 是它的操作模式。会话内按项目路径或照片路径分别缓存，保存为项目时迁移到项目路径；成功打开项目时，若会话中没有更新的草稿，则从项目的可选 `conversation_draft` 与 `conversation_draft_mode` 字段恢复。模式保存稳定标识 `edit/advice/regions`，旧项目缺省 `edit`；文字在 1.7 格式中限 4000 字，旧项目缺省为空。输入变化推进 `_edit_revision` 并标记 dirty，使显式保存和自动恢复使用同一份快照；已有草稿时切换模式也标记 dirty，空草稿时单纯切换模式仍视为导航。发送成功清空，失败保留。清空也写入会话缓存，避免旧文件的草稿在同一会话中重新出现。项目与恢复快照调用对话已有的 `_safe_text` 脱敏，避免把 `sk-` 形式的 Key 写入文件。

ConversationPane的descriptionInput使用TextArea和独立ScrollView，自动换行且输入区高度限制34～78px；助手只增加输入所需高度，单行保持原布局，长文字在输入框内滚动。Shift+Return/Enter沿原文本控件插入换行；普通Return/Enter和Ctrl+Return/Enter经sendPrompt发送，inputMethodComposing时不抢回车。成功清空与拒绝保留仍来自sendMessage布尔结果，未发送文字只沿conversationDraft存储，不产生图层或编辑历史。TextArea没有TextField的maximumLength，因此按原控件4000个UTF-16单元预算限制插入段：保留改变前共同前缀/后缀，截取可容纳的新文字，不删已存在的尾部，不截断代理对；安全文本再同步给Editor。输入焦点仍提供selectAll/undo，沿Main.textFocus隔离图片快捷键。tests/test_conversation_compose_ui.py分别覆盖两种完整窗口的实际粘贴、Shift换行/小键盘/Ctrl发送、长度和尾部/光标、拒绝/AI等待、项目/照片隔离、文字撤销重做及受控Qt预编辑事件；这些预编辑事件不替代真实Windows中文输入法验收。

`AIController.requestProgress` 只报告传输层可观测阶段：请求提交后的等待秒数、收到字节后的接收状态，以及可重试网络错误的倒计时。独立的 `progressChanged` 每秒更新这两个轻量标签，不触发整个 `Editor.changed`。重试等待也算 `busy`，由可取消的成员 `QTimer` 持有，避免旧的 `singleShot` 在用户取消后再次发出请求。对话区的状态条与主窗口底部状态栏都绑定此属性，收起助手后仍能看见进度；非流式 JSON 响应无法提供模型内部推理步骤。

`ConversationPane.followEnd` 表示跟随最新消息的用户意图，只由滚轮、拖动、滚动条、发送和“新消息”按钮改变。消息换行、虚拟委托高度估计和进度条显隐造成的 `contentY` 变化不能关闭跟随；跟随期间高度变化会延后执行 `forceLayout + positionViewAtEnd`，避免根据估计的 `contentHeight` 手工定位末尾。用户向上翻阅后保留位置并显示未读数，主动发送会恢复跟随；用户滚动可取消尚未执行的定位。

普通对象的独立pixel_worker在前台任务中读取原图并核对source_sha，segment_jobs传入原图用于细化、传入现有model_image代理用于模型编码，复用预热/磁盘embedding而不复制整张原图来缩放。后台precache继续用代理，quality.resolution=preview；正式选中时重算该对象，不把预览缓存混入原尺寸组合，完成后标为source。直接segment调用在没有代理时先缩小再转换RGB，避免额外的全尺寸RGB副本。原图matting失败时自动分割沿native_edge使用512px核心、64px上下文的有界RGB局部贴边，保留原尺寸alpha并在original_edges记录原因、显示复查提示；显式refineMatte仍严格失败并保留原范围。此后备不恢复错误粗分割的内部拓扑。

前台对象进程报告segment/edges/local_edges阶段和对象序号，总数限16；worker_bridge同时核对请求ID、请求/响应代次、当前任务类型、序号/总数、取消状态及优先级。阶段消息只能改变状态，不释放任务、标记预热完成或发布部分蒙版。保存/撤销/导出仍使用最终有效蒙版；普通模型输出保持目标名称，matting与原图贴边不把算法名称反复添加到范围标签。新模型候选及未达标边界见planning/iphoto-v1.8/selection-quality-2026-10-03.md。

`masks.validate_bitmap` 对外部 PNG 完整解码一次以拒绝损坏文件，但只缓存经过校验的摘要，不保留展开后的像素。应用自身 `encode_bitmap` 生成的 PNG 可直接登记为已校验。真正渲染时 `_decode` 使用 64 MiB 字节上限的 LRU 保存展开图，而不是按固定图片张数缓存；项目中存在多张 24MP/60MP 蒙版时，这避免了校验阶段的内存累积。`document.RASTER_CACHE` 另缓存已应用笔画/羽化的栅格结果，同样有独立的 64 MiB 上限。

源照片成功切换后，UI `session._opened` 和主 worker 的 `open` 提交分支分别调用 `clear_decode_cache()`，释放旧照片的展开蒙版。失败的打开请求不触发清理，当前照片和缓存仍可继续使用；已校验摘要索引仅占小量内存，可以跨照片复用。细节图块进程在照片切换时由既有 `stop` 流程结束。

大项目恢复写入由 `Editor` 持有的单线程 `ThreadPoolExecutor` 执行，QTimer 在主线程取回结果。自动恢复只保留最新待写快照，写入器一次只处理一个文件；快照在主线程深拷贝，后台不得读取可变的图层状态。切图、关闭和后端同步 `saveProject` 先等待正在写入的恢复副本，再按原子写入流程保护当前文档。界面使用 `saveProjectAsync`：与恢复副本共用串行写入器，按钮显示“保存中…”，完成后才更新项目路径/dirty 并清理旧恢复副本。保存期间仍允许编辑；`_edit_revision` 区分保存快照与后续修改，后续修改保持 dirty 并重新触发自动恢复。保存期间暂不切换照片/项目；退出时等待保存结果，再确认恢复副本持久化。`projectSaveCompleted(path,error)` 报告文件实际写入结果，不能用请求入队成功代表保存成功。

QML 的另存入口是 `ProjectSaveDialog`。它预填 `suggestProjectPath()` 给出的未占用绝对路径，提交后关闭，让用户可继续编辑；后台 `projectSaveCompleted` 失败时自动以原路径和行内错误重开，直接 Ctrl+S 的保存失败也进入此面板。该面板的浏览器只负责选路径，项目实际写入仍必须通过 `saveProjectAsync`；不要把请求成功当作文件已写成。

照片导出的进程在实际读取源照片、合成图层、写入暂存文件前分别发送有界NDJSON进度（同请求id，type=progress，整数phase 1～3）。`export_process.read` 处理分段/批量行，保留1MiB累计输出上限；只接受当前未取消请求、终态前且向前推进的合法阶段，界面文字来自固定阶段表。进度不发布文件、不发送exportCompleted、不释放busy。导出仍要求进程正常结束与唯一匹配的成功结果，并复核源SHA、暂存路径和目标约束，最终由主进程发布后才完成。兼容仅返回终态的旧导出进程。`Editor.exportProgress` 仅在有当前导出请求时暴露阶段；弹窗加载动画与取消按钮随pending显示，不显示估算百分比。

全尺寸合成在 `engine.render` 对已是 RGB 的照片复用输入，只在 LUT 边界确有精度修正需求时才创建原像素数组；`document.render_nodes` 延迟复制输入画布，并在蒙版确为不透明全图且图层透明度为 100% 时直接接收调整结果。组、局部蒙版与非满不透明度仍走复合路径；修复由专用的_render_healed_layer先合并笔画、填充和局部调色，再对整个层效果应用一次opacity，避免修复绕过不透明度或调色被重复减弱。这减少大图导出的整图副本，同时保留原图像素级输出契约。

引擎1.7.2-smoothing-grid沿用1.7.1加入的精确通道曲线：对仅含exposure/warmth/tint的颜色配方，这三项的增益只依赖配方和各自输入通道，_channel_lut按同一_transform_linear浮点公式计算全部256个输入字节，编成Pillow RGB point表；避免生成65³×3三维LUT、整图校正数组及转换像素数组。两个缓存仍各有12项上限，逐通道表每项含768个0～255值的Python元组（实际对象字节数不等于768）；颜色键先移除DETAIL_FIELDS，锐化/柔化/磨皮按原detail_size随后处理，RGBA仍恢复源alpha。任何contrast/highlights/shadows/saturation/vibrance/whites/blacks非零都使用完整三维LUT与边界参考校正，不能把跨通道或亮度耦合误作独立曲线。预览、细节、导出使用同一个render入口；简单颜色操作与精确浮点参考逐字节一致。tests/test_channel_curves.py核验全部输入字节、跨通道组合、RGBA、七种非独立字段、细节滤镜和配方往返。

磨皮使用由原照片尺寸决定的smoothing_step=max(1,round(max(size)/1600))整数采样间距，网格从原图(0,0)开始。render_detail_tile有磨皮配方时把带滤镜余量的outer边界向外对齐该网格，再按同一canvas_size/canvas_box合成；最后严格裁回用户请求矩形。_detail按这个固定整数间距降采样，双边滤波后按同一间距还原；只有照片右/下边缘的不足一个格子用BORDER_REPLICATE补齐，再裁回实际尺寸。不能再分别round(crop_width/scale)、round(crop_height/scale)，否则裁块大小/位置会改变重建相位，原像素视图也会与全图导出不同。该改动是新的采样规则，故更新ENGINE_VERSION；强度和图层范围仍用原配方字段。tests/test_smoothing_grid.py在RGB/RGBA、强度35/80、锐化/柔化组合、局部层/组不透明度、奇数源边缘和不同裁块下严格核对整张合成裁片，用户大图Qt输入及原尺寸PNG导出证据见第六十一轮。

普通局部调整由`engine.render_masked`按已栅格化并乘图层强度后的实际非零边界渲染：颜色是逐像素变换；磨皮、柔化与锐化累加保守滤镜余量，再根据canvas_box的源坐标向外对齐整数磨皮网格，最后只粘回蒙版边界。空范围直接返回副本；含余量的裁片达到输入面积80%时复用原整图路径，减少裁片分配。源图、蒙版与RGBA alpha保持原语义。`render_nodes`仅将普通非填充/修复/组层送入此路径；组继续递归合成后应用外层范围，组内普通调整仍可裁片。主预览缓存、原像素细节与全尺寸导出共同使用它。引擎版本和工程字段保持不变，因为像素结果与原整图算法严格相等。25项专项在独立整图基准下核对两条颜色路径、细节组合、连续alpha、RGBA、羽化位图、真实parent_id组强度、步长1/3及边缘/平移；另检查实际滤镜输入变小与大范围回退。大图用户操作、同状态前后整图像素摘要与实际PNG导出见第七十七轮。

交互PNG按用途设置压缩等级3：detail_worker的颜色和蒙版使用DETAIL_PNG_COMPRESSION，主worker的原图代理及调色代理使用PREVIEW_PNG_COMPRESSION；输出仍为无损、带sRGB的RGB/RGBA，资产文件最多8项。导出使用独立export_image的格式与编码策略。第五十九轮细节裁块的level3对level6编码时间约减半、临时文件增加约4～5%；第六十轮实际1068×1600代理的独立五次保存中位数从183/204ms到80/84ms，文件增加约3.5/3.9%，均逐字节检查解码像素。这些比例只代表本次照片与机器。

process_runtime是纯标准库启动模块；run.py经load_main按已有优先次序选择main/detail/export/warm/pixel/matte角色，再导入目标模块。主图片、细节、导出角色在没有显式OPENBLAS_NUM_THREADS、OPENBLAS_DEFAULT_NUM_THREADS、GOTO_NUM_THREADS或OMP_NUM_THREADS时默认OPENBLAS_NUM_THREADS=1；任何已有值（含空值）都保留。GUI在首次导入iphoto.app期间临时设置同一默认值，finally在导入成功/失败时恢复继承环境；返回的main随后才创建Qt、Editor及子进程，因此GUI已经初始化的NumPy为1线程，模型进程仍继承自己的原始配置。warm/pixel/matte角色不套用图片默认值。必须在NumPy首次导入前执行，已经加载后再改环境变量不等价。

tests/test_process_runtime.py通过实际run.py协议与退出观察钩子读取已加载OpenBLAS的getter，检查默认1、显式2、RGBA像素、色彩信息和全尺寸导出；另在新解释器中实际导入GUI模块、创建QGuiApplication/QProcess，核对GUI默认1但环境已恢复，Qt子进程与同一继承环境下的独立NumPy控制进程配置/线程数一致，显式2仍保留。导入失败的finally也单独检查。真实run.py完整窗口、用户大图和实际模型预热验证见第六十二轮。跨进程sRGB配置只忽略LittleCMS生成的ICC创建时间字段，其余字节核对。该策略在当前Windows非OpenMP OpenBLAS构建上验证，不据此声称所有BLAS构建都可控制。官方变量规则见[OpenBLAS文档](https://www.openmathlib.org/OpenBLAS/docs/runtime_variables/)。

仅细节滤镜的render在只读阶段复用RGB输入，由实际磨皮/柔化/锐化创建新的输出；零配方仍复制，RGBA仍在独立输出恢复alpha。磨皮对已是RGB的输入直接取NumPy字节，避免额外RGB转换副本；整数格无需补边时复用数组，需要补边时只在降采样期间保留padded，再立即释放。降采样结果拥有独立像素，原图不被原地修改。tests/test_detail_buffers.py检查三种滤镜的RGB/RGBA源不变、alpha保留及结果可独立编辑，结合固定网格、原尺寸导出和范围外不变测试验证。这些调整保持1.7.2算法像素结果，未另改采样规则。

普通位图的load_source独占本次Image.open解码对象，使用ImageOps.exif_transpose(in_place=True)在该对象上归一化方向；RGB转换或ICC转换已经返回独立像素，直接成为Source.image，不再额外复制整张图。文件上下文关闭后，Source仍拥有可读、可修改的图像，不能返回依赖已关闭文件的惰性解码对象。alpha、源摘要和白名单EXIF仍按既有流程保留，RAW解码分支不变。tests/test_source_loading.py用八种EXIF方向及八种颜色/透明度组合对比独立转换，检查文件删除后结果可用、输出与解码对象分离、源SHA不变；真实大图方向、细节和原尺寸PNG导出见第六十四轮。该优化减少解码阶段重叠副本，不改变引擎算法版本。

主worker每个render返回stage_ms（prepare/compose/png/histogram/stats/mask）；缓存命中的跳过阶段为本请求的0值，不能沿用旧耗时。worker_bridge在允许显示的预览成功时保存Editor._last_render_metrics，其中generation是实际响应代次；连续参数中间帧不能写成当前最新代次。成功换图时清空，供内部QA关联实际交付；QML状态仍以显示帧Ready和代次/覆盖范围判断。第六十轮profile定位主要等待为临时PNG编码，记录中还观察到后台真实对象预缓存会产生追加render。阶段计时不能替代从实际输入到全部刷新完成的端到端计时。

Windows进程诊断须计入QProcess虚拟环境启动PID的实际Python子进程与conhost，不能只把启动器约5MiB工作集当作图片引擎。内存采样和CIM枚举会干扰GUI计时，必须把七次时延测量与另一次内存观察分开；退出以QProcess状态及所有对应PID的退出码核验，退出后的旧内存计数可能仍存在，不能用其推算活跃内存。private bytes是提交内存，working set是当前物理工作集，不能混用。

## 画布模块（1.4.1）

`Editor.viewport` 持有独立 `Viewport(QObject)`。成功加载照片时，worker bridge 把原图尺寸传给 `setSource`；QML 把窗口的 DIP 尺寸和屏幕 DPR 传给 `resize`。缩放比例 1.0 对应原图像素与物理屏幕像素一对一，图像在 QML 中的宽度为 `source_width × zoom / DPR`。全图始终使用最长边 1600px 的快速预览；当原图尺寸与缩放比例使代理像素明显放大时，`detail_tiles` 按可见矩形加边界缓存请求一块最多 800 万像素的原分辨率细节。`detail_worker.py` 独立加载源图，在带 128px 邻域的裁剪上合成调整层、蒙版与滤镜，再返回只包含目标矩形的 PNG；按照片代际与细节版本丢弃过期结果。`worker_bridge.preview_payload` 为代理和细节路径组装同一份草稿/分区预览状态，避免未确认调整在两种分辨率下不一致。显示复杂蒙版时，细节进程也生成相同原图区域的 RGBA 或灰度蒙版图块；QML 只在图块完全覆盖当前视口后用它替换代理蒙版，平移出图块时立即恢复代理蒙版，避免叠两层导致颜色加深。位图蒙版的源数据分辨率仍受上限约束。细节进程在最后一次请求完成后闲置 20 秒就退出，当前图块继续显示；下次移到图块外会重启进程。缩回适应画布、换图、导出或交互分割也会释放细节进程。

- `CanvasPane.qml`：画布上下文、百分比输入、适应/100%/导航按钮、帮助菜单与对话面板组合。
- `CanvasViewport.qml`：图像与蒙版定位、选区鼠标事件、比较分割线、导航手势路由。优先处理中键/空格/抓手/缩放，普通选区事件继续交给下层；不通过切换选区工具实现临时抓手。
- `ViewportDetailImage.qml`：把细节图片、矩形、照片身份、文档代次和细节版本绑定到实际显示帧；异步加载期间保持上一帧及其坐标，Ready后一起切换。
- `CanvasOverlays.qml`：把对象轮廓和笔迹映射到屏幕坐标，纹理尺寸始终等于可见画布；不可重新放回大尺寸 photo Item 中分配 Canvas。
- `CanvasNavigator.qml`：缩略图与可视矩形、点击/拖动定位；复用已有代理，保持帧、失败时原图定位，按屏幕像素限制图片缓冲。
- `viewport.py`：缩放锚点不漂移；窗口重排保持源图中心；平移保留可见边缘；窗口失焦/输入文字/打开弹窗时清理临时键与手势。只有本窗口事件会被过滤。

主画布的异步Image使用`retainWhileLoading`保留同一照片的上一帧，`previewReady`仍只表示当前请求的图片已就绪，不能以旧帧仍可见允许确认未加载的结果。Loader以`editor.originalUrl`为照片身份：该URL在一次打开期间稳定，新照片的唯一原图代理URL触发重建Image并取消旧加载，不能跨照片保留纹理。按住看原图使用已加载的原图比较覆盖层，不切换编辑预览的source，因此不会重启加载或暂停更新；松开继续显示当前结果，分割位置保留。Image.Error时显示完整原图与失败说明，隐藏蒙版覆盖但保留其显示偏好、选区和文档；后续成功帧自动恢复。错误不修改配方、历史、代次或正式图层。该行为由当前固定Qt 6.10.2支持的[Image.retainWhileLoading](https://doc.qt.io/qt-6/qml-qtquick-image.html#retainWhileLoading-prop)实现，仅额外保留最长边1600px的代理帧。`tests/test_preview_continuity_ui.py`用本机延迟/失败图片服务检查真实QML像素、键鼠比较、晚到结果、换图和失败恢复，不能把受控图片响应当作AI或分割质量证据。

主画布Image另以`hasFrame`记录本照片是否有可显示结果，Error/Null清空该标志；`originalFallback`在首次结果尚未就绪、失败和失败后的重试期间持续显示原图，隐藏正式蒙版、对象预览和智能点。`hadFailure`保持失败说明，Loading时改为重试提示，直到Ready才清除；重试开始不把原图撤掉或提前恢复蒙版。Loader换照片时两个标志都重新初始化，不把旧故障或旧帧带到新照片。

导航图采用相同的照片作用域和`retainWhileLoading`，编辑帧未曾就绪或失败时使用当前原图代理；失败后重试仍保持原图，不把旧图当作新结果Ready。定位区域按原照片比例计算，独立于瞬态纹理状态；原图尚未就绪时禁用定位。编辑与原图Image都以导航区域乘Screen.devicePixelRatio设置[Image.sourceSize](https://doc.qt.io/qt-6/qml-qtquick-image.html#sourceSize-prop)，实际窗口中的照片矩形已保持原比例，不再由PreserveAspectFit优化另行放大加载尺寸。源代理和原尺寸导出不变。该预算限制导航图片缓冲，PNG解码中间开销和整个进程内存峰值未在本轮测量。`tests/test_navigator_continuity_ui.py`在完整窗口检查加载中的真实像素、点击/拖动、过期帧、真实换图、404和延迟重试、隐藏期间完成加载，以及实际加载尺寸。

对象悬停属于当前范围操作及当前元素清单：Main以未选中图层、无前台任务/对话框/菜单/分区/比较约束`objectPreviewEnabled`，该作用域和`sceneRevision`变化均清除清单hover id；打开照片也明确清除。CanvasOverlays只接受当前作用域内的对象，并在按住原图、加载失败及重试等隐藏时清除悬停归属，工具切换清除画布hover id。原图查看与比较同时隐藏对象预览、临时笔画和智能提示点；只是隐藏，不修改文档中的点、草稿或蒙版偏好。空Canvas不显示，避免残留上一次绘制。临时空格平移仅隐藏有效预览，保留仍在当前作用域内的清单归属。

元素清单使用`SceneRowsModel`而非每次Editor.changed重设QVariantList。同一清单的勾选、轮廓或状态变化只发送对应行dataChanged；清单revision或id顺序改变才reset。行悬停由`HoverHandler(blocking:false)`观察，右键由只接受RightButton的`TapHandler`处理；名称及状态属于同一Button，勾选仍由CheckBox处理。HoverHandler禁用/退出/销毁时仅清除自己的id，跨行切换时旧行不能清掉新行。保留`Editor.sceneObjects`列表接口用于命中与其他消费者；稳定行模型不拥有分割缓存或正式文档。`tests/test_object_hover_scope_ui.py`在两种窗口尺寸检查实际QML像素、行对象身份、作用域和文档不变；offscreen中的entered/exited信号由用例控制，原生鼠标悬停仍由独立窗口平台用例验证。指针Handler属于行的非视觉子对象，测试从当前视觉delegate的直接children查找，避免跨已删除delegate取到旧QObject。

SceneIndex.rows从精确缓存派生pixelWarnings，只用于当前清单反馈，不写进可移植catalog。SelectionGuide按pixelStatus与warnings显示待准备/准备中/可选/需检查/需重选；长名称在按钮内省略，状态仍可见。对象选择complete从所有选中及排除对象的缓存读取质量警告（包括本次新结果），elapsed_ms仅累加本次实际返回项，避免缓存警告丢失或把历史计算时间重复收费。草稿及自动建层均保留警告，修边面板提供补点/排除点入口。tests/test_scene_object_actions_ui.py覆盖完整窗口的真实名称/勾选/右键点击、修正入口、长名称与稳定行、恢复进度及新前台通知优先，以及混合缓存/新结果/排除对象的警告与原子撤销；合成测试蒙版不作为模型准确率证据。ppp真实项链蒙版及用户可见反馈见第六十九轮。

范围草稿存在时，调整入口由DraftOutputBar与SelectionController.applyDefault统一拥有：新范围建层、已绑定范围保存到原层。InspectorPane隐藏另一个会重新获取元素缓存的批量调整主按钮；对象菜单保留加入/减去/相交，以及明确的“用勾选对象重新选择范围”。该菜单栏从70px收为46px；草稿中没有勾选对象时隐藏。取消草稿恢复直接批量调整按钮。底层adjustCheckedObjects仍表示显式重新获取勾选对象，原自动事务、缓存和失败保护保持；界面不再让该动作与已修正范围的提交并列为主入口。tests/test_draft_object_output_ui.py在两种完整窗口中检查当前修正位图建层/原层保存、明确对象组合保留孔洞、取消恢复批量入口和一步撤销。第七十轮真实大图补点/排除与建层前后对照证明输出使用修正位图，而非原低置信度缓存。

细节交付明确区分当前文档代次、目标细节版本及已交付帧版本。render请求失效旧蒙版、保留上一块颜色图及其原矩形，并立即请求同一编辑状态的可见区域；仍只运行一个细节请求、保留一个最新待处理请求。最新结果严格核对照片/代次/版本；连续参数范围内的中间颜色帧还必须核对源SHA、版本不超前、已显示的(代次,版本)不倒退，仅保留自己的真实元数据且不发布蒙版。范围以外的旧结果不发布。QML在source变化时保存请求元数据，纹理Ready后才更新显示矩形，不能让旧纹理跟随新backend.detailRect移动。覆盖整个视口的中间或旧颜色帧可显示，并提示“正在更新细节，暂显上一次效果”；局部图块仅在与当前代理属于同一文档代次且细节版本有效时显示，避免局部新旧效果混合。当前版本、实际覆盖视口且确实可见才令detailReady为true；原图覆盖或加载失败不接受该状态。蒙版还必须与当前颜色帧的矩形匹配，不能先到的新蒙版套在旧颜色帧上。失败时退回快速预览，换图/适应/停止时清源，不复活旧图。保留帧可能增加一个至多800万像素的颜色缓冲，本轮未测进程内存峰值。tests/test_detail_continuity_ui.py用完整窗口、真实初始细节与本机受控PNG交付核对像素、坐标、乱序、蒙版、原图和故障回退；tests/test_continuous_preview.py补充真实连续键鼠输入及中间帧边界。ppp大图实际调色复测记录在第五十八及第六十五轮。

导航不进入项目或撤销，也不提交主编辑 worker 的合成任务。首次detailWanted从false变为true时，CanvasViewport通过Qt.callLater在下一次界面事件中重新确认仍需要细节后调用requestDetail，去掉首次放大的固定180ms等待；必须离开属性绑定计算后再发出控制器changed通知，避免detailWanted绑定循环。后续平移、缩放仍等待约180ms稳定并合并连续请求，控制器继续只保留一个执行中和一个最新待处理请求。调色的细节请求随render提交，不再被后续进度changed信号反复延迟。tests/test_detail_ui.py在两种窗口中实际点击100%检查首次请求及时发出，随后连续缩放不重复即时派发，最终真实图块Ready且历史不变。选区点仍用相对于完整 photo 的归一化坐标；缩放、窗口重排、空格切换发生在笔画中途时，取消未提交笔画，避免把两个坐标系拼成错误选区。

Editor.imageWorkBusy集中原有本地前台图片任务、导出中止和修复准备保护；busy仍为ai.busy或imageWorkBusy，文档编辑禁用规则保持。detail_tiles.request使用imageWorkBusy加独立warm进程状态，不把云端等待当作本地计算占用；本地准备完成后可以在AI仍等待时加载当前区域。不能简单用“AI忙则跳过所有busy保护”，因为本地任务与AI生命周期可能重叠。浏览始终读取带照片摘要/文档代次/细节版本的当前快照，AI结果改变文档时仍失效旧蒙版、重新合成；tests/test_ai_browse_ui.py覆盖真实QML放大/平移/取消、编辑继续禁用、本地任务重叠保护、AI调整交付像素和一步撤销。真实大图云端及冷本地准备证据见第六十八轮。

detail_tiles.release仅释放源图进程、执行/等待任务与协议缓冲，保留已交付颜色、蒙版、矩形及真实版本；照片warm和前台点选请求使用它，闲置park也复用。stop在换图、适应视图及原有必须清图的路径中先清帧、推进版本，再release。退出或晚到回复不能发布被释放的任务，已保留的当前图块不会仅因进程结束重新解码。warm仍阻止新细节请求；移出已覆盖区域时CanvasViewport说明“照片正在准备，完成后加载当前区域细节”，完成后加载最新视口。进入smart/object且尚无选区草稿时，仅在工具切换时关闭当前层蒙版覆盖，重复点击同工具保留Q显示偏好；已有草稿和真实结果仍显示选区。tests/test_photo_prepare_ui.py覆盖真实初始图块、完整QML按键/按钮取消、平移后恢复、照片身份和任务优先级、后台队列与晚到结果。ppp两张大图的真实本地模型流程和Windows子进程退出见第六十七轮。

`test_viewport.py` 覆盖几何与高 DPI；`test_canvas_ui.py` 使用真实 Qt 键鼠/滚轮事件，检查输入焦点、草稿不变、坐标对应、导航图、比较线和两种窗口尺寸。`scripts/benchmark_canvas.py` 单独测量软件渲染帧及同步读回时间，不能当作显示刷新率或真实 FPS。

## 图层组的确切语义

项目仍使用有界的平铺记录列表，每项有稳定 `id`、`kind`（adjustment/group）和 `parent_id`，从这些关系构造真实树。相同父节点的列表顺序是从下到上；界面反向显示。支持四级组嵌套，合计 32 项。拒绝循环父链、不存在的父节点、普通调整层作为父节点及重复 id。

组内各层先对进入该组的照片依次调整，再将组结果通过组蒙版和不透明度与进入组前的照片混合。组不透明度只应用一次。隐藏父组会隐藏所有后代；组蒙版外保持进入组前的像素。组没有直接调色参数，须在组内新建调整层。

这是调整图层组；尚未实现 PSD 导入、独立像素图层、智能对象、剪贴蒙版和 Photoshop 的所有混合模式。折叠仅改变显示，不影响合成；复制组会重新分配所有后代 id；删除组包含后代，整次动作可撤销。

## 添加一项能力

AI 局部分区携带有界 `mask_target`（`object` / `face_skin`），`ai_tasks.parse_regions` 校验并贯穿局部定位、像素任务和原子建层。旧回复中明确命名“面部/脸部/脸颊/人脸”的磨皮区域兼容推断 face_skin，其余保留 object；未知类型或显式 null 拒绝。面部定位保留初始上下文 `skin_crop`，不把脸颊小框直接作为模型图片，以免失去嘴唇等部位的上下文。AI 请求包含当前面部分区能力状态。

Inspector的参数滚动区不足280px时使用紧凑头部：直方图32px，快捷按钮保留，省略其独立重复撤销说明；避免固定范围修正栏让初始曝光滑杆被裁在屏幕外。该布局只随实际可用高度变化，不重新定位用户滚动位置、不修改图层或历史。连续拖动测试核对整个滑杆位于实际裁切区域，再核对多帧、原像素和一步撤销；本轮小窗口大图流程通过。

`segmentation/face_models.py` 固定作者发布的权重大小与 SHA，`setup_face_parsing.py` 显式安装，UI 不下载。`face_skin.py` 在独立像素进程使用已核对摘要、方向和色彩的原图局部做 BiSeNet CPU 推理，取定位点对应的皮肤连通域，要求鼻部语义支持，排除眉眼嘴唇等类别并给保护区留一个模型像素余量。连续透明度仅向内过渡，原图尺寸位图使用 alpha 采样保存；分辨率不代表模型语义一定正确。纯面部任务不加载 EfficientSAM，也不被它的预热阻挡、不误标其缓存就绪；混合分区仍完整成功后一次建层，失败、过期和取消沿原像素事务保护。主 worker 保留等价兼容入口。真实 ppp 原图、本地回放的历史云端规划、专用模型、状态及取消证据见第七十九轮。

1.5 的像素后端入口是 `segmentation/service.segment`。当前 `EfficientSAM.predict` 接收 PIL 图像和原图尺度的坐标/标签，返回候选 logits、模型评分和计时；服务负责约束检查及位图编码。`models.py` 固定权重清单，`runtime.py` 处理 Windows C++ DLL 版本一致性；两者不触碰系统文件。`classical.py` 保存原 GrabCut/魔棒/U²-Net 实现，`plugins.py` 保留元数据和旧函数兼容出口。完整依据与边界见 [分割模块设计](../planning/iphoto-v1.5/selection-research.md)。

`test_pixel_selection.py` 测协议、提示约束和位图边界；`test_pixel_workflow.py` 测真实 Qt 输入路由及取消/过期/失败保护。旧协议事务测试显式使用 `pixel_protocol_stub`，不计入模型质量证据。`test_v14_ui.py` 和 `test_redesign.py` 的对应案例在权重存在时通过真实 worker 推理；首轮 CPU 编码使用 60 秒测试期限。自然照片对照用 `scripts/qa_pixel_selection.py`，几何真值测量用 `scripts/qa_segmentation_geometry.py`，二者均为真实模型。

照片预热由 `pixel_selections.warm` 启动 `warm_worker.py` 短进程，读取主 worker 生成的 1600px 代理 PNG，把 EfficientSAM 嵌入写入同一磁盘缓存。预热完成后，按需启动 `pixel_worker.py` 持久进程处理所有交互分割与元素轮廓预计算；主 worker 继续负责照片导入、预览和导出。交互点选优先于后台轮廓；若后台轮廓正在计算，结束像素进程并重排该对象，重启后先处理点选。取消正在执行的点选会结束像素进程，原选区不变；换照片会清理旧图的后台请求。结果按照片代际或元素清单版本校验。`scripts/qa_scene_preheat.py` 验证缓存真正从磁盘读取，`scripts/qa_cold_pixel_queue.py` 验证冷预热和执行中的取消，`scripts/qa_large_photo_priority.py` 验证后台轮廓期间的画框、换图与前台点选。

状态栏另用 `_status_epoch` 区分前台结果与后台进度。后台轮廓请求携带开始时的版本；前台请求或通知推进版本，过期的后台结果仍更新元素行缓存，却不能覆盖之后的导出路径、选区结果或错误提示。场景分析完成消息及缓存清单恢复消息显式属于后台进度，因此无后续操作时仍可由轮廓完成数替换；准备进度另给出有质量警告的对象数。缓存restore先启动precache后发通知时不能推进前台epoch，否则整批后台结果都无权更新状态。

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

通用真实 API 验收脚本使用内置湖景；本任务按用户授权也使用ppp中的指定自然照片测试，源文件只读取。Windows 沙箱可能无法访问登录用户的凭据，应在同一用户的正常 Windows 会话执行。记录请求类型、延迟、HTTP/完成状态、经脱敏的结果与覆盖图；不记录 Authorization、Key 或完整图片请求体。`workflow_cached` 模式需先有 `artifacts/v14-live-workflow/real-workflow.iphoto`。`scripts/selftest_live_ui.py --live` 驱动真实 QML 走"文字选区 → 智能细化 → 输出两分支 → 分区草稿 → 图层操作 → AI 修图/建议"全流程并留截图；Repeater 委托控件用视觉树遍历查找（`QObject.findChild` 到不了委托对象）。

性能报告区分 CPU 合成和真实云端耗时；合成数值不包含 PNG、进程通信与界面显示。64 MiB 是缓存的逻辑像素预算，不是整个应用的内存上限。对象悬停使用 384×384 索引，不做逐帧分割或云端请求。


## 1.6 透明边缘模块

`matting/trimap.py` 管前景 / 背景 / 未知约束，顺序处理已知前景和背景的小孔洞连通域，避免同时保留两份整图标签；`matting/solver.py` 管有资源边界的 CPU 数值求解，服务调用时直接写入 uint8 透明度输出，浮点输出仍可用于数值验证；`matting/service.py` 管文档输入输出并在编码前释放不再需要的整图数组。`controllers/matting.py` 管草稿事务，`controllers/matte_process.py` 管独立一次性细化进程、请求代际和取消；`matting/worker.py` 读取原照片并验证 SHA 后调用服务。正常 UI 请求不占主编辑 worker，取消可直接结束细化进程并保留草稿；主 worker 的旧 `matte` 操作仍为兼容入口。`segmentation/` 继续负责对象语义与初选，两者不能混为一类模型分数。

新灰度 PNG 可带 `sampling: "alpha"`。`masks.py` 统一处理插值及零覆盖保护，`document.raster_mask` 统一处理手工修正和内羽化。当前项目 schema 1.7 接受旧格式；修改资产字段须同时检查验证、保存恢复、画布预览和原图尺寸导出。

“调色效果”预览由 `controllers/worker_bridge.preview_payload` 在深拷贝图层里替换草稿蒙版，供快速预览和原图细节共用。不要把临时预览写回真实层，否则会破坏撤销和取消。

真实求解、半透明边缘与小孔洞测试在 `tests/test_matting.py`。`scripts/qa_alpha_matting.py --natural` 生成已知 alpha 和真实山林对照；`scripts/qa_matting_ui.py` 通过真实 Qt 输入触发求解和预览，`scripts/qa_matting_cancel.py` 覆盖运行中取消、立刻重试、画框和换图。输出在 `artifacts/alpha-matting/`。范围、性能、未实现的颜色补偿见 [1.6 技术说明](../planning/iphoto-v1.6/alpha-matting.md)。


## 1.8 选区重构与新协议字段

选区交互的单一门面是 `controllers/selection_controller.py`（`editor.selection`）：工具、模式、笔刷、容差、蒙版显示与任务状态机都在其中，QML 只读绑定并经它下发；`draftBegan/draftEnded/toolChosen` 是视图联动的唯一信号源。共享组件在 `ui/components/selection/`（输出条、修边组、查看方式、任务条、工具选项条）。范围模块直接进入调整，修边与替换已有层范围作为附加操作；批量勾选对象直接生成调整层，自动修图也无需先手动选区。分析与设计见 `doc/selection-analysis.md`、`doc/selection-redesign.md`，实施与验收见 `planning/iphoto-v1.8/`。

`TaskStatusBar` 仅在 `Main.qml` 工具栏下方实例化，脱离对话框和检查器滚动/焦点状态。前台AI、像素、细化任务及照片准备共用 `SelectionController.taskKind/taskText/taskCancellable/cancelTask`；独立背景轮廓不占前台状态。照片准备为warm，优先级低于边缘细化、前台像素/排队点选、范围细化和AI；准备原图局部细节仍归入AI任务，取消按准备token丢弃晚到结果。Editor.photoPreparing只反映当前照片且未放弃的warm进程（含Starting），不写入文档/历史。取消warm同时终止准备及像素进程、移除关联低优先级队列，把当前清单未完成对象标为unavailable，保留已有精确蒙版，防止退出回调立即重启编码。状态条保留 `aiRequestProgress/aiRequestProgressText/cancelAiRequest` 稳定名称，AI说明任务名称、等待秒数与可观察阶段；SelectionController单独转发ai.progressChanged，避免只有editor.changed时才刷新秒数。侧栏和聊天不再各自重复取消按钮，通知浮层排在任务条下方，避免遮挡下一项进度。Esc沿同一门面取消，输入框或模态对话正在使用键盘时不抢快捷键。

右侧检查器没有标签页：导航由 `SelectionController.pickedLayerId` 驱动——未选中图层时显示统一范围模块 `ui/areas/SelectionGuide.qml`（工具芯片、文字识别、元素清单行悬停=画布预览/点击=设为范围/＋−=叠加减去，走 `selection.rowSelect`；修边卡），选中图层时显示 `AdjustmentPane`（头部"返回范围"取消选中）。图层行点击=选中/再点取消；草稿开始自动取消选中；`apply/applyRegions` 落地后自动 pick 新层，"范围→调整"零切换。固定的 `DraftOutputBar` 发出 `refineRequested` 导航信号，由 `InspectorPane` 负责把 `SelectionGuide.refineCard` 滚入视口；子组件不直接修改滚动容器。元素清单与选区源同属一个模块，不再存在独立 ScenePane；旧经典面板与标签行已删除。

快捷效果的参数定位也沿视图导航处理：`AdjustmentPane.revealParameter(key)` 按组的 keys 定位并展开相应组，发出 `parameterRevealRequested(row, section)`；`InspectorPane` 在窗口下一次 `frameSwapped` 后用实际布局计算并限制滚动位置，随后清除请求。不用固定短定时器猜测展开后的内容高度。请求带当前 pickedLayerId；返回范围、换层或进入分区草稿时丢弃过期目标。只在显式快捷操作时定位，后续参数更新不自动抢回用户的滚动位置，不增加文档历史。参数行按各组 keys 排序，人像组先显示磨皮。大小窗口、同值重复应用、拖动与撤销、手动滚动、过期导航由 `tests/test_preset_focus_ui.py` 验证；实际 ppp 大图证据在问题记录第六十三轮。

协议新增字段均向后兼容：mask 可选 `edge_shift`（int，-5~5，短边百分比，`raster_mask` 用 PIL Min/MaxFilter 收缩/扩张）；layer 可选 `inpaint:{method:"telea"|"ns",radius:1~25}` 与 `heal:{ops:[{kind:"heal",points,radius:MIN_STROKE_RADIUS（0.00001）~0.2}]}`。两者都在 `render_nodes` 渲染时合成（先修复/填充，再套 recipe 与蒙版），因此非破坏、可撤销、可随项目保存；图层组不允许携带这两个字段。修改这些字段须同时检查 `validate_layers`、`preview_cache.node_key/layer_key`、`render_nodes` 与导出链路。

修复笔画有独立范围：heal_region_mask仅来自笔画，普通mask继续限制同层调色/填充，保留旧的空蒙版修复层语义。_render_healed_layer以原输入为最终强度混合的基准；先生成100%工具效果，再以round(255*opacity)一次混合，预览缓存与源尺寸细节均复用这条路径，组仍在外层控制整体效果。`inpainting.inpaint_image` 先按既有>8阈值取修复范围的bbox，扩展2*ceil(radius)+4像素上下文后才创建RGB/BGR数组并调用OpenCV；空范围不调用求解器，范围尺寸不符直接拒绝。输出仅粘回局部、保留RGBA原alpha，全尺寸范围复用直接输出，避免额外全图副本。两个OpenCV方法、边缘/多孔洞/全图范围与原全图算法的逐像素比较均有专项。

SelectionController分别保留蒙版与修复画笔半径，默认0.025/0.003、上限0.15/0.025。存储协议MIN_STROKE_RADIUS为0.00001，仍严格拒绝非有限、非正和越界值；UI下限按原图短边换算为1px半径（2px直径），并钳制不超过对应上限。brushRadius读取时也按当前源图钳制，切换源尺寸不会读取越界设置。brushDiameterStep按当前像素直径的10%，修复至少1px、蒙版至少2px；SpinBox与方括号共用该步幅，setBrushDiameter只改变工具设置。没有改动光栅化、OpenCV求解或旧项目笔画解释；AI局部修复及其精确笔画蒙版也沿共享协议接受更细半径，原坐标/面积/重叠/部位边界校验保持。切换到修复关闭蒙版覆盖，不创建范围草稿、不修改文档或历史；模式按钮和蒙版画笔设置仍沿既有路径。

`SelectionController.correctMask(add/subtract)`是已有层精确修正的入口：拒绝忙碌/分区/已有草稿及非法模式，复用reviewMask绑定当前层，切到绿色覆盖与对应画笔模式。InspectorPane在滚动区外提供固定“补选范围/擦除范围”；Main清除比较状态并聚焦画布。保存/取消沿原绑定事务完成，Inspector仅临时记住源层与原滚动位置，draftEnded后的绘制帧再恢复；换照片清空，不写入工程或历史。属性定位在整节超出可见高度时按目标行底部定位，确保小窗口锁定后的最后一个色彩控件可见。CanvasOverlays的Rectangle圆圈按画笔半径与当前照片显示比例绘制，MouseArea接收画笔悬停；Alt/Shift经Viewport同一键状态通知改变预览颜色，失焦/文字/弹窗沿resetKeys清理，平移、比较和弹窗时隐藏圆圈。24项tests/test_mask_brush_ui.py覆盖真实双窗口点击、保存/取消与滚动返回、尺寸输入/方括号/独立工具记忆、缩放与修饰键反馈、冲突状态保护；ppp真实面部范围的手工嘴唇线修正、原像素及工程证据见第七十八轮。此入口改善范围修正，不代表自动皮肤解析器已经能排除五官。

`worker.py` 的主循环是 op 注册表（`register("open"/"render"/...)`）；新增 worker 操作只需注册一个处理函数。worker 的 `open/render` 响应附 `stats`（通道均值与亮度 1/50/99 分位数，编码空间），供 `controllers/auto_adjust.py` 的本地自动调整使用——它是直方图规则，不是 AI。`document.RASTER_CACHE` 是蒙版栅格的 64 MiB LRU（与预览缓存预算各自独立），`render_nodes` 经 `raster_mask_cached` 复用；worker 在 `open` 时清空。蒙版字典按版本不可变是缓存正确性的前提：任何就地修改蒙版的代码都会造成脏命中。

真实 UI 全流程自测用 `scripts/selftest_live_ui.py --live`（读取 Windows 凭据中的千问配置，参数不接受 Key）；Repeater 委托控件（如 `parameter_exposure`）不在 `QObject.findChild` 可达范围，脚本用视觉树遍历查找。

## 智能选区的原图细节

`matting/models.py` 固定 ViTMatte-S 导出文件、官方权重修订与摘要；`scripts/export_matting.py` 在独立转换环境生成固定640图，`setup_matting.py` 原子安装并验证，本地UI不下载模型。Windows使用DirectML发行包，其他平台CPU包；它们共享导入路径，`setup.ps1`先检查本项目应用已关闭，再清除旧发行包，避免安装冲突。

`matting/neural.py` 是像素进程专属单例session；512原像素核心/64上下文，RGB按官方0.5均值/标准差、trimap按255归一化，不放大输入细节。只输出未知像素，known 0/255保持；固定缓冲、未知像素/块数/超时预算，DirectML失败后同一输入仅重试CPU一次。`segmentation/detail.py` 只在明确正负提示、足够颜色差异与小排除域时重开局部内部结构，按点击颜色簇处理细枝混色，不把单个大背景全部变成unknown。局部范围外保留旧alpha，提示不合规不提交部分结果。无可靠结构条件时采用既有窄带trimap的神经透明度，缺模型/失败时保留此前原图CF与RGB后备；显式PyMatting请求仍严格失败，不偷偷换模型。

前台通用对象进入上述链路，背景catalog仍用代理并在正式选择时升级；皮肤任务有独立分支。对象NDJSON进度新增details与可选tile/tiles：前台ID/代次/对象匹配，1～128块严格校验后只更新状态，不结束任务或发布部分mask。取消终止原独立像素进程；保存、原像素预览及导出复用同一最终bitmap。`test_neural_detail.py`覆盖切块坐标、约束、孔洞、混色、坏模型、安装原子性与CPU重试；自然照片模型质量证据见选区调研第三轮与UX记录第九十一轮。

`segmentation/confidence.py` 优先用分割模型原始logits排序生成稀疏语义参照，不用RGB阈值判定细节。连通域几何仅提出小区域：面积不超过照片15%、含上下文ROI不超过2百万像素、最多四处；模型最确定5%为内部种子，弱或均匀字段拒绝扩大内部未知区。logits不是校准概率。原图局部直接取RGB，置信度只重采样到有界ROI，避免为24/60MP照片放大整张float图。普通边缘与所有局部共享4百万未知像素、128块、180秒预算和单调N/M进度；最终提示点检查通过才编码发布。未形成可靠计划时继续现有边缘/显式局部约束路线。只有AI定位框的请求沿实际模型内部锚点校验，不增加用户提示点或历史。`test_confidence_detail.py`覆盖这些事务、预算、坐标和约束；自然照片及完整入口证据见第九十二轮。

已有精细bitmap与分区bitmap的智能修边优先沿`details → Editor.refineDetails → matting.start(method="neural")`进入独立一次性matte进程，直接用ViTMatte估计已有轮廓的窄带alpha；显式SAM重新识别和经典PyMatting仍单独可选。`neural.refine(points=...)`校验提示、冲突锚点和已知范围，未知区内的锚点固定为0/255。完成时恢复点列表，把最终mask用作下一次补点的空间参照；分区不带入独立选区的点。模型session各进程隔离，不在UI执行推理。

matte进程的prepare/details进度绑定请求ID、op、双代次与源SHA，严格整数/1～128块/单调序号/固定总数校验，仅更新状态；不清理active或提交部分蒙版。取消终止该进程，立即重试待旧进程退出后启动新任务；晚到回复不能替换新照片。`test_matte_progress.py`覆盖协议、最终提交与真实坏源/坏输入子进程，控制器与神经测试覆盖路由、锚点、范围和元数据；完整入口与SegRefiner候选对照见第九十三轮。

## AI 操作已有五官范围

1.9.15的`ai_mask_refinement.py`定义`refine_mask`工具与局部点协议，`controllers/mask_refinement.py`编排裁切、云端定位和神经结果的原子发布。只允许已有明确nose/lips分区；当前草稿与指定已有层两种作用范围不能混用，目标层的配方与锁定独立校验。原图worker的`mask_refinement_crop`从实际alpha边界外扩64px，4MP预算后准备原图、L蒙版、覆盖对照，去源元数据；`mask_points`请求按此顺序发送三张图，私有路径与其他配方不发送。

AI仅输出1～5个局部排除点，要求位于当前白色蒙版内，核对数值／尺寸／重复位置后按像素中心映射原图；实际修正沿`semantic_refine`的SAM2 dense先验路径，保留原零区与部位信息。源SHA、代次、token、层快照／锁定、草稿／绑定在各阶段一致才发布。草稿只修范围使用草稿历史；已有层的蒙版与颜色一起进入一次层历史。准备、云端、像素三阶段均经任务门面取消，过期／失败必须清理pending，晚到结果不再请求AI或修改文档。`test_ai_mask_refinement.py`覆盖这些边界，真实千问和24MP原应用证据见第109轮；该工具不恢复漏选或隐藏边缘。

1.9.16在神经结果后增加`ai_mask_review.py`的视觉复查；结果先暂存，完整事务最后统一发布。4MP内alpha支持以1～4px邻接生成最多16个已有候选，主体内部最深点所属区域受保护，几何只编号。原图／L蒙版／编号覆盖对照交给AI，keep／remove／uncertain严格解析；最多删除8个明确且未保护的编号，未知／重复／主体编号拒绝。原图worker的`mask_refinement_apply`重算核对公共清单与源SHA，仅清除指定候选的原覆盖，框内其他alpha及主体保持；CV／NumPy在实际处理时导入。token新增stage防止迟到的旧阶段结果或错误改变新阶段。此为1.9.16旧行为；1.9.17最终质量核对的reject／uncertain、失败与取消均保留开始前范围与参数。用户显示短结果句，AI内部编号／字段不进入产品说明；详细响应留诊断。`test_ai_mask_review.py`覆盖协议、精确alpha、状态、主体保护与事务，实际双照片／取消／原像素证据见第110轮。

点协议的`PointLocationError`只标记合法结构的点落在已有黑区或重复像素。一次点位纠正仍失败时，`AIController.maskPointsUnavailable(generation)`触发原范围的一次区域复查；有效unsupported点计划走同一路径。尺寸、结构、非有限／越界坐标、网络／鉴权、取消均不走此后续路径，不吸附或猜点。原范围复查keep／uncertain不改配方、不消费绑定草稿、不加层或历史，明确说明保持；remove只清理AI明确指出的未保护候选，与可选配方统一提交。这条路径未运行SAM2，不在质量提示中冒称神经修正。复查自身非法或失败仍有界结束，不循环重试。


1.9.17在所有实际减少覆盖之后统一进入`verify_preparing → verify`，包括神经修正和原范围区域清理路径。`mask_validate`只返回accept／reject／uncertain与观察说明，使用单独的质量任务，不发送用户关于误选的断言、图层配方、历史或候选编号；仍使用用户保存的同一个模型与套餐端点。图像顺序为原图、修改前绿色、修改后绿色、红色减少量、可选完整脸部关系；同一局部裁切按原/新范围并集取帧，RGB对照最多放大3倍且最长边1024，原灰度蒙版和坐标尺寸保持原像素。脸部图仅提供上下文，不参与目标范围推断或绑定。

混合区域复查提出reject／uncertain时，变化候选也交给单独质量任务确认，期间不再整片清理；没有变化则直接保持。这样避免其把边缘减少误述为整片主体消失便终止有效修正。`publish`拒绝未经核对的变化结果；最终accept后范围与配方一次提交，reject／uncertain保持全部开始前状态并用answered说明原因。不变的原范围后续路径不增加一次无用核对，也不消费草稿或发布配方。验证失败最多校正一次，取消、代次变化、锁定或文档变化沿既有事务拒绝。语义核对减少误删风险，不提供真值精度保证；第108～110轮1607视觉判断已更正，协议与数量记录保留。

worker资产缓存保留当前完整对照批次与现有三个显示资产，六图批次最多需九个文件。新批次释放旧批次，打开新源清空批次保留集，`_prune_assets`按实际保留集调整数量上限；避免固定八个文件上限删除尚待UI编码发送的原图。状态、取消、迟到结果、像素α、资源存活与真实大图证据见第111轮和`tests/test_ai_mask_review.py`。

1.9.18的`mask_refinement.method`允许`exclude`和`boundary`，旧两字段请求仍按exclude解析。前者沿已有云端排除点流程；后者仅在当前范围/目标层提供对应可用标志时允许。`boundary_context`检查明确nose/lips、精细模型及唯一人脸关系，内部只给像素任务传crop/features/anchor，不给云端原始标签、像素蒙版或源SHA。`part_boundary.py`从原RGB和FaRL LaPa原类别分别找上下唇12/13、鼻部10的深内部保留点；嘴内11已有黑区可加排除点，再以SAM2.1 Small和已有dense先验生成候选。缺失任何必要保留点、模型或关系即保持，不退回弱模型猜部位。

该任务要求前台原图、空外部点列表；人脸上下文最多16MP，已有范围外扩64px的局部最多4MP。只在局部放置语义字段，不分配整张原图float标签。guided edge后alpha取与原alpha最小值，未选区、孔洞、部位元数据保持，不能补漏选。变化才编码并清空已展平的ops；无变化返回原mask完整结构。随后复用review/verify和完整事务，跳过云端猜排除点这一步，不跳过质量核对。语义保留点进度沿ID/op/代次/任务优先级校验，只改状态，无部分发布。

boundary未改范围且未调色时用answered说明保持，不添加历史或无用复查请求；同范围但显式调色经过既有复查后只提交参数，结果不声称修正了范围。测试覆盖草稿/绑定/已有层、锁定、取消、拒绝、无变化及精确alpha；第112轮真实原应用结果包含成功与拒绝，并证明同一候选的视觉核对不稳定。保留点只保证锚点，SAM评分和原像素尺寸均不代表通用语义精度。未改变项目1.9、颜色引擎、模型权重或依赖。

## 初始五官范围的连续边缘

1.9.19的`face_precision.FaceParser.predict_part`返回原类别图与连续五官边缘，`predict_native`保留类别返回契约。`native_part_alpha`在原类别目标外扩64px内以64×1024块还原11类logits再softmax，嘴唇合并作者上/下唇类别7/9，鼻部6；把0.25～0.75的目标类别分数作为过渡。其他确定类别保持零，只有目标和邻近面部皮肤可形成过渡；这是本项目的边缘策略，不是校准概率或物理透明度估计，也不新增原图细节推理。

`face_skin.segment`仅在精细部位返回合法连续字段时使用，整脸/整脸皮肤与基础模型保持原路径。对应人脸连通范围、父身份、显式空间范围和鼻部眉眼嘴保护仍共同约束alpha；嘴唇不再把所有皮肤类别先膨胀进保护区或再次做二值内缩。当前alpha字段始终与原裁切同尺寸，错误或空字段不发布。初始化的部位可覆盖原硬收缩遗漏的边缘，不改变已经保存的蒙版，也不放宽已有AI范围修正的只减约束。

同脸原类别与float32分数共享缓存，键包含RGB内容、尺寸和五点变换。缓存数据总上限16MB；直接保存放不下时仅将uint8类别图无损zlib压缩，分数不降精度，仍超限则不缓存。复用时类别图只读并精确还原，后续部位不用再次推理。该上限是缓存数据，不代表进程总内存或峰值。`face_continuous`进度只更新状态，沿原ID/op/代次/优先级和取消契约；单位/原图/完整应用及失败驱动记录见第113轮与`tests/test_face_continuous.py`。

## AI补回五官漏选

1.9.20新增`mask_refinement.method=restore`。`face_skin.segment`新生成的nose/lips蒙版带可选`face_part_scope`：只包含最初hint的空间字段，经`spatial_mask/validate_mask`白名单验证，禁止嵌套范围、语义和人脸关联元数据。原有结果、手动笔画、智能修边、matting路径只要仍保留部位类型，就保留这份原始限制；项目1.9保存/重开保留，不改变alpha渲染或颜色引擎。旧项目可正常打开，但没有原始限制的旧部位暂不可restore，不能凭标签推断整脸范围；旧版重新保存会丢掉这个可选字段。

`restore_context`仅给有原始限制、唯一完整人脸和精细模型的目标提供可用标志。原始scope及其bitmap不会进入云端规划上下文。补选不需要SAM或EfficientSAM，排除/边缘工具的模型门槛仍分别校验。前台原图任务`part_restore`在像素worker经`face_restore.restore`运行FaRL连续部位分区，保留父人脸关系和原始scope，精细字段失败不退到基础模型。候选取原alpha和重识别alpha的最大值：每个旧像素不得降低，新增alpha不得超过原始scope。变化才展平ops/feather/edge_shift；无变化保留原mask全部字段，不添加无用核对。16MP人脸上下文/4MP部位核对预算保持。

补选绕过减少范围专用的排除点和区域清理，直接进入独立`mask_restore_validate`。原图/旧绿色/新绿色/蓝色连续增加量/可选完整脸关系使用同帧，前后覆盖强度相同，RGB最多放大3倍至最长边1024，native L几何不变。任务只看到中性部位说明，不带用户“漏选”判断、配方或清理编号。严格accept才原子发布；reject/uncertain/取消/过期/失败保留开始前范围、颜色、锁定、层与历史。已有`mask_validate`仍只核对减少量并拒绝增加，补选不得混入旧减少协议。

源SHA/代次/token/stage/完整层快照与草稿绑定沿既有编排；各阶段有状态，最终核对也可取消。主动取消标记cancel_requested，由终止通知完成原请求，显示assistant/answered与范围参数保留；真正失败仍failed。草稿只补范围用草稿历史，已有层/绑定层的范围与可选颜色一次进层历史。`tests/test_face_restore.py`覆盖范围来源、严格方向、协议、拒绝、事务、源变化和模型门槛，第114轮完整应用实测记录成功/取消和驱动错误；没有真值，不把孔洞恢复视为自然复杂漏选的准确率证明。

## 明确完整意图下的五官重选

1.9.21的`mask_refinement`增加独立严格对象`method=reselect, intent=whole_visible_part`；旧exclude/boundary/restore对象保持。规划只在用户明确要求完整可见鼻部或上下嘴唇时调用，局部嘴角/孔洞/原限制请求不可扩大。程序校验声明，不独立解释自然语言意图；实际局部请求见第115轮。`reselect_context`要求精细模型、唯一人脸/特征，384代理旧支持必须完整落在对应脸hint内，且不交叠另一人脸；云端只获可用标志，不发送scope/bitmap或源SHA。

前台原RGB任务`part_reselect`沿`face_restore`共享严格FaRL连续分区；不接受外部点、其他部位方法或后台代理，精细失败不降级。完整脸hint生成新`face_part_scope`，候选允许同时新增和减少，但部位/目标/绑定/native尺寸不得变化，alpha不得越新scope。16MP脸关系、4MP新旧局部并集预算保持。变化才替换bitmap并展平ops/feather/edge_shift，α相同只更新来源；旧restore仍只增且遵守旧scope，原减少校验没有放宽。

重选直接进入独立`mask_reselect_validate`，同时看新范围的完整可见主体及红色减少/蓝色增加：原图、旧绿色、新绿色、红色损失、蓝色增益、可选完整脸黄色上下文，同帧/同强度。变化量分别减法截零，RGB最多3倍至1024，native L不变。七对照加三个显示资产最多十个文件，由当前批次动态保留集管理，前批次释放。只有strict accept才原子更新范围/可选颜色/scope；拒绝、疑虑、取消、失败、过期保留完整开始状态。范围像素未变只记来源时不冒称修好；完全无变化无调色不添历史。公开摘要说明核对范围并提示边缘检查，不展示内部图号或精准断言，原始响应保留诊断。`tests/test_face_reselection.py`与进度/资产测试覆盖事务和门槛；第115轮记载原始大图自然旧蒙版重建、真实AI/取消/局部请求，以及失败驱动与无真值限制。

## 通道曲线与完整配方字段

1.10.0增加`tone_curves.py`与`CurveEditor.qml`，项目写入1.10并继续读取0.1及1.2～1.9。Recipe新增curve_rgb/red/green/blue四条控制点序列；内部不可变tuple可作缓存键，序列化为JSON数组，默认空数组保持原值，两端默认直线归一为空。非空2～16点、整数0～255、首尾输入0/255、输入严格递增，拒绝重复/非有限/越界，不擅自排序或夹取。包含中间点的中性直线保留锚点，便于后续编辑。RANGES继续只描述40个数值滑杆；RECIPE_FIELDS共44项，RECIPE_PROPERTIES统一数值与曲线schema。单层/分层/已有层/五官修正全配方验证和锁定均使用完整字段，不能遗漏曲线或误把数组当数值。

曲线在编码sRGB中应用，位于原明暗/增益/HSL之后、空间细节之前，先RGB总曲线再每通道。使用PCHIP的局部保形三次插值，横轴为对应通道值，不是按照片亮度划分区域；控制点本身可非单调，插值不越相邻输出范围。系数缓存最多64条，纯通道路径仍精确256级C查表；HSL或跨通道明暗混合曲线走有界192行精确条带，避免陡曲线通过粗3D LUT失真，RGB总曲线逐通道处理控制临时内存。没有新运行时依赖。render/reference、局部范围、原像素与导出共享计算，ENGINE_VERSION更新使旧预览缓存失效。

CurveEditor在原调整面板增添可折叠“通道曲线”，选择RGB/红/绿/蓝，点击加点、拖动、删除中间点、输入/输出精确值与重置。实时拖动沿参数预览节奏，松手finishGesture只提交一步；Esc还原本次原曲线及原锁并不增历史，保存状态仍沿既有编辑标记。点位UI严格限制输入顺序，两端输入固定，输出可移动。SelectionController.applyCurve在提交前核对当前层/照片/文档代次/权限，失败不写文档；cancelCurve同样检查绑定。坐标文字只留在UI草稿，错误不会夹取或写历史；回车/失焦/保存/导出经commitText校验，换层/照片/后续编辑/忙碌丢弃过期文字。AI修改曲线通过parameterFocusRequested定位此曲线；手动锁可明确解锁交还AI。第116轮记录实际大图、真实AI、性能和失败，tests/test_tone_curves.py用独立SciPy插值核对形状/像素/协议，tests/test_curve_ui.py使用完整QML鼠标键盘验证操作；这些测试不读取个人凭据。
