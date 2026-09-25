# 本地选区模型

## EfficientSAM 像素分割（1.5）

基础依赖包含 ONNX Runtime 1.24.4；显式运行 `.venv\Scripts\python.exe scripts/setup_segmentation.py` 安装 S；添加 `--variant ti` 安装用于对比测试的 Ti。

权重位于 `models/segmentation/`，S 两个文件合计 106,124,065 字节；Ti 合计 41,365,489 字节。编码器/解码器来自[作者官方 Space](https://huggingface.co/spaces/yunyangx/EfficientSAM/tree/d8dbb1eee73bfb3392aa6f6e8944aeb13f3f4036)，固定该修订；文件大小和 SHA-256 全部列于 `src/iphoto/segmentation/models.py`，下载及推理加载均验证哈希。

作者：Yunyang Xiong 等，EfficientSAM，2023；ONNX 拆分导出贡献 Kentaro Wada。来源与许可：[EfficientSAM Apache-2.0](https://github.com/yformer/EfficientSAM/blob/main/LICENSE)、[ONNX Runtime MIT](https://github.com/microsoft/onnxruntime/blob/main/LICENSE)。后续分发安装包须保留第三方版权与许可文件，以及现有 Qt/PySide 的许可要求。

模型只接受图像和点/框，不读取用户文字。千问负责把文字转换成目标位置，本地分割负责像素。无需 GPU；实际首次 CPU 编码耗时见 v1.5 验收报告。并非发丝 matting 模型。当前应用默认 S，Ti 通过 QA 脚本选择。

## U²-Net 显著主体（已有能力）

当前适配 U²-Net 小模型（u2netp）的 ONNX 输出，使用已安装的 OpenCV DNN 在 CPU 推理，无需另装 ONNX Runtime；此模型用于主体/背景区分，不是文字指令分割器。

项目基础依赖安装后，显式下载可选模型；应用本身不会首次运行时自动下载：

```powershell
.\.venv\Scripts\python.exe scripts/setup_subject.py
```

随后重新打开 iPhoto，或在“图像能力”里重新检测。模型固定在此目录的 `u2netp.onnx`，不会从 `.iphoto` 项目加载可执行插件。

来源：[U²-Net 官方仓库](https://github.com/xuebinqin/U-2-Net)、[rembg ONNX 模型与校验定义](https://github.com/danielgatis/rembg/blob/main/rembg/sessions/u2netp.py)。U²-Net 和本版 OpenCV 代码 Apache-2.0。模型文件 4,574,861 字节，SHA256 为 `309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8`。分发时应同时核对权重来源与相关许可，保留第三方说明。

320×320 模型输入决定了细节上限；即使将蒙版输出放大到预览尺寸，也不代表发丝级精度。主体选择失败时请使用框选、魔棒、画笔或后续分割后端。
