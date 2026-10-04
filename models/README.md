# 本地选区模型

## EfficientSAM 像素分割（1.5）

基础依赖包含 ONNX Runtime 1.24.4（Windows 使用支持 CPU 的 DirectML 发行包）；显式运行 `.venv\Scripts\python.exe scripts/setup_segmentation.py` 安装 S；添加 `--variant ti` 安装用于对比测试的 Ti。

权重位于 `models/segmentation/`，S 两个文件合计 106,124,065 字节；Ti 合计 41,365,489 字节。编码器/解码器来自[作者官方 Space](https://huggingface.co/spaces/yunyangx/EfficientSAM/tree/d8dbb1eee73bfb3392aa6f6e8944aeb13f3f4036)，固定该修订；文件大小和 SHA-256 全部列于 `src/iphoto/segmentation/models.py`，下载及推理加载均验证哈希。

作者：Yunyang Xiong 等，EfficientSAM，2023；ONNX 拆分导出贡献 Kentaro Wada。来源与许可：[EfficientSAM Apache-2.0](https://github.com/yformer/EfficientSAM/blob/main/LICENSE)、[ONNX Runtime MIT](https://github.com/microsoft/onnxruntime/blob/main/LICENSE)。后续分发安装包须保留第三方版权与许可文件，以及现有 Qt/PySide 的许可要求。

模型只接受图像和点/框，不读取用户文字。千问负责把文字转换成目标位置，本地分割负责像素。无需 GPU；实际首次 CPU 编码耗时见 v1.5 验收报告。并非发丝 matting 模型。当前应用默认 S，Ti 通过 QA 脚本选择。

## 原图细节透明度 · ViTMatte-S

智能选区在模型已配置时自动复用 EfficientSAM 的对象轮廓、置信度排序和原照片细化透明度。小区域只保留模型最确定的少量内部参照，其余内部结构交给 ViTMatte 判断，可靠条件下无需补充排除点；颜色阈值不参与这条路径的像素分类。大区域、弱或均匀的模型输出保留普通边缘细化；已有明确正负点的高反差局部约束仍作后备。相近背景、低置信边界、复杂透明材质仍需检查，ViTMatte不是独立通用语义分割器。最多四个局部区域与普通边缘共同受未知像素、推理块数和时间预算约束，完成后一次交付。

模型来自[官方 ViTMatte](https://github.com/hustvl/ViTMatte)，使用官方 Transformers 内置实现与固定 HF 修订 `6a58ad7646403c1df626fbd746900aec7361ea1d`。本项目导出固定640×640 ONNX（512核心、64原像素上下文，不缩放原图细节）。文件103,959,533字节，SHA256为 `dbbe16723638209f1883d1499060f43e249afe55a2411a70bfb6e7932560ffdb`，安装和进程首次加载均校验。[MIT许可](../docs/licenses/ViTMatte-MIT.txt)。运行应用无需Torch/Transformers；模型不随Git提交，也不在UI中自动下载。

首次生成在独立转换环境进行，避免改动应用依赖。固定转换器已在本机重复导出并验证同一SHA；不同版本未通过校验时不会安装。

```powershell
python -m venv artifacts/matting-converter
artifacts/matting-converter/Scripts/python.exe -m pip install torch==2.10.0 torchvision==0.25.0 transformers==5.18.0 onnx==1.23.1 Pillow==12.3.0
artifacts/matting-converter/Scripts/python.exe scripts/export_matting.py --output artifacts/vitmatte-small-640.onnx --cache-dir artifacts/matting-model-cache
.venv/Scripts/python.exe scripts/setup_matting.py --from-onnx artifacts/vitmatte-small-640.onnx
```

重新打开应用后生效。Windows依赖配置使用 `onnxruntime-directml==1.24.4`，神经透明度会优先用可用的DirectML设备，启动/推理失败后重试CPU；EfficientSAM仍指定CPU，保持缓存与定位行为。CPU版与DirectML版共享Python导入目录，更新用 `scripts/setup.ps1`；先关闭应用，脚本先移除旧发行包再安装当前依赖。[微软要求](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)采用顺序执行、禁用memory patterns、同一session单线程调用，当前像素进程遵守这些约束。

## 面部皮肤分区 · BiSeNet

显式运行 `.venv\Scripts\python.exe scripts/setup_face_parsing.py`，在 `models/face-parsing/resnet18.onnx` 安装 53,205,364 字节的权重。来源为[yakhyo/face-parsing 作者发布](https://github.com/yakhyo/face-parsing/releases/tag/weights)，SHA256 固定为 `0d9bd318e46987c3bdbfacae9e2c0f461cae1c6ac6ea6d43bbe541a91727e33f`，下载和首次加载都校验。第三方说明保存在 [MIT 许可](../docs/licenses/face-parsing-MIT.txt)。

AI 分区使用 `mask_target=face_skin` 指定单个人脸，独立像素进程读取并核对原照片摘要，裁出脸部上下文后使用512×512模型区分皮肤、鼻子、眉眼、嘴唇、头发、帽子和衣物。内部点所在的皮肤连通域会成为独立图层范围；唇眼等区域保持零透明度，边缘仅向内过渡。蒙版保存原图尺寸，但模型边界依然有精度限制，需要检查遮挡和细节。手臂、天空和普通物体继续使用 EfficientSAM，不能把此模型用于全身皮肤。缺失或识别失败时保留照片与已有层，不回退成整个人物磨皮。

`mask_target=face` 选择包含五官的完整面部，排除颈部、头发、帽子和衣物；`face_skin` 只保留皮肤、鼻子与连通的耳部。模型误分类仍可能发生，尤其在帽檐与侧脸处。由本地检测器定位时，若鼻部定位点落在遮挡物上，只允许使用检测框内模型实际识别到的可见鼻部恢复定位；无法找到时保留旧范围。语义蒙版记录目标类型，后续 AI/经典修边都保护已有零覆盖度，避免重新填入五官孔洞。

局部五官共用 `segmentation/face_parts.py` 的目标契约：`face_part=nose` 搭配 `face_skin`，只保留鼻部类10；`face_part=lips` 搭配 `face`，只保留上下唇类12/13，排除嘴内类11与周围皮肤，两者均要求 `face_scope=region`。类别对应[作者定义](https://github.com/yakhyo/face-parsing/blob/main/utils/common.py)。先确认鼻点所在完整面部，再筛目标类别；已定位的人脸上下文和用户范围共同限制编辑边界。嘴唇与整脸磨皮分开建层，AI唇色规划必须关闭磨皮，不能用 `object` 猜测嘴唇轮廓。已有模型即可使用“选鼻部皮肤/选嘴唇”本地入口，不增加下载。遮挡、表情和小脸仍可能造成类别遗漏，不能识别隐藏部分，也不把嘴内类11称为独立牙齿类别。

皮肤范围还参考人脸检测器的眼睛与嘴角定位，为分区模型误判的五官增加局部保护。只使用落在合理面部类别上的可见定位点；落在帽子、鼻子或背景的眼部猜测不扩展为保护孔洞。完整人脸模式不使用这些孔洞。局部保护采用保守椭圆，可能连带少量邻近皮肤，不能等同于五官级真值。

## 精细五官 · FaRL LaPa（1.9.10，可选）

鼻部和嘴唇入口在精细模型已配置且有五点定位时自动使用FaRL LaPa；没有配置时沿用BiSeNet。完整人脸外轮廓继续使用19类BiSeNet。1.10.2的完整／局部皮肤范围先做BiSeNet分区，再在同一人脸内部用FaRL皮肤／鼻部类别分数和眉眼／嘴唇类别细化，减少旧定位保护圈误挡皮肤；必须在当前脸框内、两模型共享唯一可见鼻部连通域。眼镜、耳饰、颈部、帽子、衣物、头发和外部边界仍由19类模型保护，耳部沿原范围，不用缺少这些类别的LaPa覆盖外轮廓。原图局部预算16MP、共享16MB语义缓存，无新增权重。模型异常、尺寸／对应不可靠或预算超限时提示并保留基础分区；现有保存蒙版不重算。

权重为[FacER作者模型发布](https://github.com/FacePerceiver/facer/releases/tag/models-v1)的 `face_parsing.farl.lapa.main_ema_136500_jit191.pt`：646,604,126字节、SHA256 `f5a874906795ef89fadd7cf3b5b218ed8550fa9dbb383b7c0f95726c3a352914`。五点对齐、448输入、warp_factor=0.8和类别映射依据[固定作者源码](https://github.com/FacePerceiver/facer/blob/ddd35c76ff840174b8a5403ad1c1255e37b8782b/facer/face_parsing/farl.py)，保留[FaRL许可](../docs/licenses/FaRL-MIT.txt)与[FacER许可](../docs/licenses/facer-MIT.txt)。不是发丝透明度模型。

隔离转换器读取已校验的官方Torch 1.9 JIT参数，按原推理算子导出固定1×3×448×448 ONNX。采用torch==2.6.0+cpu、onnx==1.17.0，重复导出同一摘要，645,022,368字节、SHA256 `60a239ea923ec79d26d966015ef2b462e13d3963f8d551fc7c15bae6d7fff279`。转换前对照官方JIT分数；最终应用只使用NumPy和现有ONNX Runtime，CPU最多四线程。输入采用原图EXIF/ICC读取流程，512输出分数先逆变换到原像素再分类，64×1024分块；没有先放大类别图。单人脸语义缓存最多16MB，核对实际像素、尺寸和五点变换，鼻部/嘴唇连续操作复用，换图或定位改变即失效。

已有已验证ONNX可直接安装：

```powershell
.venv/Scripts/python.exe scripts/setup_face_precision.py --onnx artifacts/farl-lapa-448.onnx
```

从官方源转换时使用单独的Python环境，不给应用安装Torch：

```powershell
python -m venv artifacts/face-converter
artifacts/face-converter/Scripts/python.exe -m pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu
artifacts/face-converter/Scripts/python.exe -m pip install onnx==1.17.0
.venv/Scripts/python.exe scripts/setup_face_precision.py --python artifacts/face-converter/Scripts/python.exe
```

`--source`可复用已下载的官方JIT文件；大小和摘要不符时不加载、不安装。权重位于 `models/face-parsing/farl-lapa-448.onnx`，不入Git，不在UI下载。当前机器已安装，重新打开应用后使用。较大的模型增加第一次等待及内存占用；遮挡侧脸上唇仍可能带入周围皮肤，小脸也可能漏掉细唇，须放大检查，不能称为任意照片精确五官选择。

## 精细排除点 · SAM2.1 Small（1.9.14，可选）

1.11.3补充用途：对话通道抠图的独立效果检查可以用此已配置模型进行一次局部纠错，至多两个512px边缘及96px上下文，由AI核对明确保留／排除参照；随后用ViTMatte求透明度并再次检查，未通过不提交。它不替代普通物体选择的默认模型，不取消面部语义保护。真实头发仍有灰雾，见[纠错测试](../planning/iphoto-v1.8/matte-correction-2026-10-05.md)。

明确的鼻部、嘴唇分区含排除点时，可使用[Meta 官方 SAM2.1 Small](https://huggingface.co/facebook/sam2.1-hiera-small)结合分区蒙版进行神经修正。没有模型或旧蒙版没有 `face_part` 信息时沿用 EfficientSAM；旧蒙版可重新选择鼻部/嘴唇取得部位信息。整脸、磨皮皮肤、身体和普通选物不切到此模型。真实整脸排除测试出现碎孔，不能把嘴唇排除成功推广到整脸选择。

运行时仅用现有 ONNX Runtime CPU，最多八线程，主环境无需 Torch。局部 RGB 按作者方式以浮点双线性缩放到1024，蒙版变为256×256连续 logit 提示；单掩膜预测关闭动态多掩膜稳定性切换、自动填孔和小块删除。结果仍需满足全部提示点、与原范围有交叠、保留原零区与裁切区外像素；低置信提示检查，不作为准确率。单局部编码缓存核对实际RGB与尺寸，不缓存点预测。首次模型加载、原图局部编码和预测分别显示状态，可取消。

固定[官方源码](https://github.com/facebookresearch/sam2/tree/2b90b9f5ceec907a1c18123530e92e794ad901a4)，检查源码Git blob摘要；使用[Microsoft官方ONNX封装](https://github.com/microsoft/onnxruntime/tree/v1.24.4/onnxruntime/python/tools/transformers/models/sam2)，核对五份封装文件SHA。保留 [SAM2 Apache-2.0](../docs/licenses/SAM2-Apache-2.0.txt) 和 [ONNX Runtime MIT](../docs/licenses/ONNX-Runtime-MIT.txt) 许可。固定官方checkpoint版本 ee5bba1d82bb8749febdf90f45e84b687142ba03，184,416,285字节，SHA256 `6d1aa6f30de5c92224f8172114de081d104bbd23dd9dc5c58996f0cad5dc4d38`。

本轮使用隔离torch2.10.0、onnx1.23.1、onnxruntime1.24.4转换，opset17。重复导出摘要相同；七张照片对照原作者CPU模型，硬分类变化0，不代表人工真值准确率。安装后文件在 `models/precise-points`，不入Git：

| 文件 | 字节 | SHA256 |
|---|---:|---|
| sam21-small-encoder.onnx | 138,018,674 | 73f90685bf0a2a252a6cdf04d5bd5e0f77a96aada8b0c47dd328fc420bb45194 |
| sam21-small-decoder.onnx | 16,564,074 | 0abf6e5a21ce63ee4faad1943ef676d7985b8d1f5fa66c1115e787f631daa31b |

已有验证导出时：

```powershell
.venv/Scripts/python.exe scripts/setup_precise_points.py --onnx-dir artifacts/precise-points-onnx
```

从官方来源转换时，提供包含上述固定版本和SAM2作者依赖的独立Python环境。脚本显式下载固定源码/checkpoint、校验、转换、同时核对两个输出，再原子安装；不会覆盖未知已有文件：

```powershell
.venv/Scripts/python.exe scripts/setup_precise_points.py --python artifacts/point-converter/Scripts/python.exe
```

可同时指定 `--checkpoint` 和 `--sam2-source` 复用已下载的固定来源。全部大小和摘要匹配才安装；应用界面不下载。当前机器已配置，重新打开应用后加载。首次自动识别上唇仍可能选入皮肤或漏掉细唇，SAM2修正不能恢复原分区已排除的像素；需要放大检查，扩大范围可补选。部位信息随项目、修边和笔画保存，不把局部鼻部/嘴唇当作整脸磨皮层；旧应用重存会丢此可选信息。

## 人脸检测 · YuNet 与 RetinaFace

显式运行 `.venv\Scripts\python.exe scripts/setup_face_detection.py`，安装并校验以下两个模型。已有YuNet环境可以再次运行，安装侧脸补充；应用本身不下载权重。

固定 [OpenCV Zoo 作者模型](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet)，文件 `models/face-detection/face_detection_yunet_2023mar.onnx`，232,589 字节，SHA256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`；下载和加载均核对大小/摘要，保留 [MIT 许可](../docs/licenses/YuNet-MIT.txt)。当前使用 OpenCV 4 的模型及输入缓冲接口，避免中文路径问题。

侧脸补充使用[作者 RetinaFace MobileNet0.25 ONNX](https://github.com/yakhyo/retinaface-pytorch/releases/tag/v0.0.1)，文件 `models/face-detection/retinaface_mv1_0.25.onnx`，1,736,694字节，固定SHA256 `b7a7acab55e104dce6f32cdfff929bd83946da5cd869b9e2e9bdffafd1b7e4a5`。预处理、先验与解码依据[作者代码修订](https://github.com/yakhyo/retinaface-pytorch/tree/4cd6e3471e5bac794637290a530566f463db4762)，保留[MIT许可](../docs/licenses/RetinaFace-MIT.txt)。CPU ONNX Runtime，最多四个推理线程，首次使用才加载，不增加Torch依赖；安装和首次加载均校验摘要。

打开照片在图像进程上做最长边1280px的本地检测，阈值0.8，最多16个人脸；YuNet没有可靠结果时尝试±30°旋转输入，将定位映射回原图。随后用已配置的RetinaFace补充检查遗漏，包括原检测已找到部分脸的情况。交叠范围还需相近鼻点才合并为同一脸，原成功定位及编号保留，新脸追加编号；已达到16脸时跳过补充。没有配置补充时沿用原流程，补充模型异常保留原结果并提示。检测框只是身份与裁切上下文，最终面部范围由 BiSeNet 计算，整个人脸和皮肤分别输出原尺寸蒙版。没有检测结果时不会生成占位矩形冒充精准选区，侧脸可继续用云端文字定位，背向不可见的脸不应被生成。补充仍不保证所有极小、拥挤或遮挡人脸都能识别。

1.9.10对旋转检测中眼睛/嘴角均挤在一起的五点估计补充校正：仅当RetinaFace与当前脸唯一匹配、置信分数高至少0.05，且两对定位点跨度明显更合理时使用它的鼻点与五官点；原框、裁切、编号、顺序不变。普通定位、歧义匹配和仍然集中在一起的候选保持原估计。此为神经检测结果的保守融合规则，分数并不等同于真值准确率。

## 身体裸露部位 · EfficientSAM 原图局部

`mask_target=body_skin` 复用已安装的 EfficientSAM-S，不需要新增权重。每个调整区域可带最多四个独立 `parts`，每项包含定位框与皮肤内部点；面部与普通物体不带此列表。云端先分别定位，独立像素进程核对源照片摘要，按各部位的初始上下文裁原图，再缩到最长边1600px推理。各部位的连续透明度按最大值合并为一张原图尺寸蒙版，重叠处不会叠加磨皮；全部完成后才交付。

这是局部物体分割适配，裸露皮肤的位置来自定位流程，模型本身不具备全身解剖或皮肤颜色分类能力。分开的手臂可以使用一个调整层，但袖口、遮挡及细发丝仍需检查，原图尺寸输出也不代表每个原像素边界都准确。各局部图分别编码并复用现有磁盘缓存，首轮多个部位需要多次编码；取消停止正在运行的像素进程，保留已有层和范围。

## U²-Net 显著主体（已有能力）

当前适配 U²-Net 小模型（u2netp）的 ONNX 输出，使用已安装的 OpenCV DNN 在 CPU 推理，无需另装 ONNX Runtime；此模型用于主体/背景区分，不是文字指令分割器。

项目基础依赖安装后，显式下载可选模型；应用本身不会首次运行时自动下载：

```powershell
.\.venv\Scripts\python.exe scripts/setup_subject.py
```

随后重新打开 iPhoto，或在“图像能力”里重新检测。模型固定在此目录的 `u2netp.onnx`，不会从 `.iphoto` 项目加载可执行插件。

来源：[U²-Net 官方仓库](https://github.com/xuebinqin/U-2-Net)、[rembg ONNX 模型与校验定义](https://github.com/danielgatis/rembg/blob/main/rembg/sessions/u2netp.py)。U²-Net 和本版 OpenCV 代码 Apache-2.0。模型文件 4,574,861 字节，SHA256 为 `309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8`。分发时应同时核对权重来源与相关许可，保留第三方说明。

320×320 模型输入决定了细节上限；即使将蒙版输出放大到预览尺寸，也不代表发丝级精度。主体选择失败时请使用框选、魔棒、画笔或后续分割后端。
