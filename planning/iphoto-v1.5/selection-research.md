# v1.5 自动选区重构：调研与实现决策

日期：2026-09-23。目标：把自动选区作为独立图像能力建设，解决“语义理解对了，但轮廓只是多边形”的根本问题。验收记录另见 delivery.md。

## 根因与行业做法

旧链路让视觉语言模型返回少量轮廓坐标，再栅格化成蒙版。增加坐标点数、平滑折线或者羽化，都不能恢复模型没有识别到的物体边界。GrabCut 可利用颜色修正粗范围，但面对相似颜色、复杂背景和孔洞时不足以承担通用语义选区。

本轮采用“目标定位 → 专用像素分割 → 边缘处理 → 正负点纠偏 → 用户确认”的职责划分。云端千问解释文字、返回目标框和内部点；本地模型读取真实照片，生成逐像素蒙版。多边形仅作为旧项目兼容的定位提示，绝不作为自动分割失败后的替代结果。

Adobe 将自动选择与 Select and Mask 中的边缘修正、Refine Hair 分开；SAM 系列提供点/框提示、多个候选蒙版及复用图像特征的交互方式。这些是本次工作流的依据，不代表我们复刻了 Photoshop 算法或达到了同等效果。

来源：[Adobe Select and Mask](https://www.adobe.com/learn/photoshop/web/make-precise-selections-in-select-mask)、[Adobe 头发选择](https://helpx.adobe.com/photoshop/desktop/make-selections/automatic-color-based-selections/make-improved-hair-selections.html)、[SAM 2 图像预测接口](https://github.com/facebookresearch/sam2/blob/main/sam2/sam2_image_predictor.py)。

## 后端比较与选择

| 方案 | 适用点 | 本项目决策 |
|---|---|---|
| 语言模型直接描多边形 | 低成本语义定位 | 仅用于读取旧数据，不再要求模型输出最终轮廓 |
| GrabCut / 颜色魔棒 | 简单颜色分离、手动辅助 | 保留为独立辅助工具，不冒充语义模型 |
| U²-Net 小模型 | 整体显著主体 | 保留，但不能代替任意文字目标和实例选择 |
| EfficientSAM S / Ti | 图像点/框分割；官方提供拆分的 ONNX 编码器/解码器 | 实际集成、对比测试；默认 S，CPU 执行，复用图像特征 |
| SAM 2.1 | 更强的交互分割及视频扩展，Apache-2.0 | 作为后续质量后端；本轮没有安装或跑分 |
| SAM 3 / 3.1 | 文本/视觉提示的概念分割，适合开放类别、多实例 | 调研候选；官方安装路径依赖较新的 PyTorch/CUDA，权重需申请访问并遵守 SAM License，本轮未接入 |
| 专用 matting | 毛发、半透明边缘的 alpha 估计 | 应作为分割之后的独立阶段；本轮的局部 RGB 边缘处理不是发丝抠图模型 |

选择依据是当前机器仅有 Intel UHD Graphics、约 32 GiB 内存，希望本地可用且无需额外云端 Key。EfficientSAM 的 CPU 可运行性不等于实时性能，也不等于行业最高精度。SAM 3 的最新视频能力不能直接推导为本机照片选区体验。

一手资料：[EfficientSAM 官方代码与 ONNX 说明](https://github.com/yformer/EfficientSAM)、[官方 ONNX 示例](https://github.com/yformer/EfficientSAM/blob/main/EfficientSAM_onnx_example.py)、[官方权重 Space](https://huggingface.co/spaces/yunyangx/EfficientSAM/tree/main)、[SAM 2](https://github.com/facebookresearch/sam2)、[SAM 3](https://github.com/facebookresearch/sam3)、[SAM 3 许可证](https://github.com/facebookresearch/sam3/blob/main/LICENSE)、[PyMatting](https://github.com/pymatting/pymatting)、[ONNX Runtime Python](https://onnxruntime.ai/docs/get-started/with-python.html)。

## 已实现的边界

1. `segmentation/grounding.py`：严格校验目标框和内部点，旧轮廓可读。场景、单目标和分区规划统一使用定位协议。
2. `segmentation/prompts.py`：生成点/框提示；零填充距离变换避免边缘对象的默认点落到画面角落。最多六个点，超过时明确提示，不静默丢点。
3. `segmentation/efficient_sam.py`：独立 ONNX 后端，固定 CPU 线程预算；同一图片的编码只做一次，下一次点选只解码。模型权重固定版本和 SHA-256。
4. `segmentation/service.py`：选择满足正负点约束的候选，拒绝空蒙版、异常值或严重偏离定位的结果；不拿模型内部预测分数当实测准确率。
5. `segmentation/edges.py`：仅在边缘小带内依据局部前景/背景颜色估计 alpha，保护确定的内部和外部、保留孔洞。不能保证头发、玻璃、烟雾等困难边缘。
6. `controllers/pixel_selections.py`：异步任务、对象组合、提示点、草稿和确认。结果取消、失败或代际过期时保留旧选区；分区全部生成成功后才出现整组草稿。
7. `scene.py`：已有像素结果用于悬停和点击命中；首次未分割对象仍显示粗定位，界面明确说明。对象像素缓存留在当前会话；已确认蒙版和草稿保存进项目。

实际测试后的两项修正：首次点选结果作为后续点的空间先验，避免第二次点击切换实例；范围外的正点可扩展提示框。对于模型忽略的小块负点区域，`corrections.py` 用固定颜色容差的连通区域修正，仅从已有蒙版中减去，累计不超过图像的 5% 和该候选的 20%，且不能包含任何保留点；否则仍拒绝结果。界面会注明进行了局部修正。这是保守的启发式补救，不是新训练的模型，也不是通用抠图保证。依据：[OpenCV floodFill 的固定范围和 mask-only 语义](https://docs.opencv.org/4.x/d7/d1b/group__imgproc__misc.html)。

纯算法模块不访问 Qt 控件、云端 Key 或用户项目。运行库兼容模块只定位 PySide6 包中的 DLL，不创建 Qt 对象。算法在已有独立 worker 内执行；取消丢弃结果，当前不能中途抢占一次 ONNX 推理。

## 安装、性能与可扩展性

显式运行 `scripts/setup_segmentation.py` 下载 S 模型；Ti 用 `--variant ti`。正常启动不下载模型。S 编码器+解码器共约 106 MB（十进制），清单在 `segmentation/models.py`，并在加载前校验内容。ONNX Runtime 固定到实测的 1.24.4。

Windows 兼容问题：本机系统 MSVC 14.31 与 Python/Qt 的 14.44 混用时，ORT 导入崩溃。`runtime.py` 在 worker 中按绝对路径预载应用内匹配版本的 C++ 运行库，保留句柄；不改系统 DLL、PATH 或注册表。新机器仍须通过实际推理检查，能力列表中的文件检测不等于推理健康保证。

选区目前在最长边 1600px 的代理图上生成，位图存储上限 2048px；原尺寸导出会放大蒙版，不能创造发丝细节。下一阶段应根据质量样本决定引入高分辨率局部重分割 / matting，以及更强硬件上的 SAM 后端，不能仅靠继续增加提示词。

后端扩展遵守 `predict(image, coords, labels) -> logits, scores, diagnostics`，服务层负责提示约束与输出校验。不同模型的分数不得直接比较；不支持的提示类型需明确报错。未来替换后端时保留 QML、草稿事务、位图格式和模型下载校验的接口。

## 验收方法

- 自然照片：保存旧多边形与真实模型的叠加对照，检查屋檐、树枝、孔洞、背景误选；没有人工真值时不报告准确率。
- 可控几何图：真实模型推理，与已知曲线/孔洞真值比较 IoU 和容差 2px 的边界 F1；结果只说明这些简单图的表现。
- 交互：真实 Qt 事件检查 S、Alt、空格抓手、点坐标、撤销提示点及确认/放弃；路由测试中的假分割器明确标注。
- 故障：模型缺失、空结果、提示矛盾、任务取消、过期响应均不得覆盖原选区。
- 真实千问：只用已有授权和内置湖景，记录脱敏结果；协议成功与像素质量分别检查。

自然图困难样本和失败记录保留，不以几何测试通过或 HTTP 200 宣称通用选区已完美。
