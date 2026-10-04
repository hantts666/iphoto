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

## 人脸检测 · YuNet 与 RetinaFace

显式运行 `.venv\Scripts\python.exe scripts/setup_face_detection.py`，安装并校验以下两个模型。已有YuNet环境可以再次运行，安装侧脸补充；应用本身不下载权重。

固定 [OpenCV Zoo 作者模型](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet)，文件 `models/face-detection/face_detection_yunet_2023mar.onnx`，232,589 字节，SHA256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`；下载和加载均核对大小/摘要，保留 [MIT 许可](../docs/licenses/YuNet-MIT.txt)。当前使用 OpenCV 4 的模型及输入缓冲接口，避免中文路径问题。

侧脸补充使用[作者 RetinaFace MobileNet0.25 ONNX](https://github.com/yakhyo/retinaface-pytorch/releases/tag/v0.0.1)，文件 `models/face-detection/retinaface_mv1_0.25.onnx`，1,736,694字节，固定SHA256 `b7a7acab55e104dce6f32cdfff929bd83946da5cd869b9e2e9bdffafd1b7e4a5`。预处理、先验与解码依据[作者代码修订](https://github.com/yakhyo/retinaface-pytorch/tree/4cd6e3471e5bac794637290a530566f463db4762)，保留[MIT许可](../docs/licenses/RetinaFace-MIT.txt)。CPU ONNX Runtime，最多四个推理线程，首次使用才加载，不增加Torch依赖；安装和首次加载均校验摘要。

打开照片在图像进程上做最长边1280px的本地检测，阈值0.8，最多16个人脸；YuNet没有可靠结果时尝试±30°旋转输入，将定位映射回原图。随后用已配置的RetinaFace补充检查遗漏，包括原检测已找到部分脸的情况。交叠范围还需相近鼻点才合并为同一脸，原成功定位及编号保留，新脸追加编号；已达到16脸时跳过补充。没有配置补充时沿用原流程，补充模型异常保留原结果并提示。检测框只是身份与裁切上下文，最终面部范围由 BiSeNet 计算，整个人脸和皮肤分别输出原尺寸蒙版。没有检测结果时不会生成占位矩形冒充精准选区，侧脸可继续用云端文字定位，背向不可见的脸不应被生成。补充仍不保证所有极小、拥挤或遮挡人脸都能识别。

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
