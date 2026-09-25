# iPhoto 1.5.0：像素选区交付与验收

日期：2026-09-23。调研依据、模型比较及后续路线见 [selection-research.md](selection-research.md)，模块职责见 [仓库说明](../../docs/repository-guide.md)。

## 交付范围

- 文字选区、画面元素和 AI 分区统一改为“千问定位 → 本地 EfficientSAM 像素分割”。最终是 PNG 位图蒙版，可有曲线、孔洞和软边；不直接使用语言模型输出的多边形。
- S：本地像素点选；普通点击保留，Alt 点击排除；新目标、退一点、框选后重新贴边、已有草稿补点。空格抓手和缩放快捷键保持有效。
- 同图复用编码和已分割对象；后续点保留首次目标的空间先验。候选必须满足提示点；对小块漏掉的负点区域进行有界颜色修正。模型失败、取消和过期结果保留原选区。
- 选择仍是独立草稿，确认后才改变图层蒙版。分区先全部计算，再作为一组草稿检查；确认整组可撤销。对话保存定位说明、分区理由和分割结果，异步后仍保留来源信息。
- 代码拆至 `src/iphoto/segmentation/`；Qt 任务控制在 `controllers/pixel_selections.py`，旧算法在 `segmentation/classical.py`，`plugins.py` 保留能力注册与兼容接口。
- 新增固定版本/哈希的模型安装脚本，已在本机配置 S/Ti。解决 Windows 系统与应用 MSVC DLL 混用导致的 ONNX 导入崩溃，未修改系统文件。

## 实测证据

### 自然照片与真实千问

使用内置 `assets/lake.jpg`，没有上传用户私有照片。S 和 Ti 都实际加载 ONNX 权重，输出天空、湖面、木屋、栈桥和远处教堂五组像素蒙版。旧多边形/新蒙版对照在：

- [S 天空对照](../../artifacts/selection-v15-s/object-3-comparison.jpg)
- [S 木屋对照](../../artifacts/selection-v15-s/object-5-comparison.jpg)
- [S 栈桥对照，含误选](../../artifacts/selection-v15-s/object-6-comparison.jpg)
- [S 计时记录](../../artifacts/selection-v15-s/report.json) / [Ti 计时记录](../../artifacts/selection-v15-ti/report.json)

外轮廓已能沿山峰、屋檐和建筑边缘变化；复杂栈桥仍有误选，细树枝周围仍有漏选。以上照片没有人工标注真值，因此不报告照片准确率，也不以模型自己的 predicted_iou 当实测 IoU。

已使用本机保存的千问 Token Plan / qwen3.8-flash 进行真实流程测试。新框/内部点协议成功解析 8 个对象。第一次天空目标请求遇到平台 HTTP 500；木屋与栈道请求成功。随后复用新清单重试，天空和木屋均生成位图草稿；两次用时分别为 37.38s、2.84s（含云端和本地流程；首次包含模型启动和编码）。失败记录保留在 [首次报告](../../artifacts/v14-live-v15-pixels/report.json)，重试在 [重试报告](../../artifacts/v14-live-v15-retry/report.json)。报告不含 Key。

### 有真值的几何图

`scripts/qa_segmentation_geometry.py --variant s` 对 600×420 椭圆和环形物执行真实推理，输入正点，环形物额外输入孔洞负点。结果见 [原始 JSON](../../artifacts/geometry-v15-s/report.json)：

| 图形 | 粗框 IoU | 像素蒙版 IoU | 边界 F1，容差 2px |
|---|---:|---:|---:|
| 椭圆 | 0.6675 | 1.0000 | 1.0000 |
| 环形物 | 0.6019 | 1.0000 | 1.0000 |

这是两个简单、高对比的合成样本，不能推广成人像、毛发或自然图的准确率。更小的 320×240 环形回归图揭示过真实缺陷：初次模型填孔，IoU 约 0.9317；加入正负点和有界修正后，真实 worker 测试要求 IoU > 0.98 且孔洞中心为 0，现已通过。失败案例推动了代码修改，没有降低最终修正后的质量门槛。

### 性能与界面

本机 Intel UHD Graphics / 约 32 GiB RAM，全部 CPU 推理，ORT 内部 4 个计算线程。以下为单次测量，不是性能保证：

| 路径 | 首次编码 | 后续目标（分割服务，含边缘/PNG） |
|---|---:|---:|
| EfficientSAM-S | 15.69s | 0.79–0.92s |
| EfficientSAM-Ti | 6.80s | 0.82–0.94s |

另需约 2–3s 模型加载。Ti 更轻、更快，但少量照片不能证明任一模型全面优于另一模型；应用默认 S，Ti 留在对照脚本中。

Windows 原生窗口通过 QTest 真实点击 S 与 Alt，使用真实模型、内置照片且不连接云端。第一次显示结果 24.36s，第二次 3.09s；当时还有回归任务占用 CPU，不能和上表当作同一负载直接比较。结果和两个点见 [原生窗口截图](../../artifacts/selection-v15-ui/real-click-1.png)，计时见 [UI 报告](../../artifacts/selection-v15-ui/report.json)。过程中图层未改变，未出现 QML 警告。

### 自动回归

最后一轮回归结果在本文件末尾记录。协议/事务测试中的 `pixel_protocol_stub` 显式标注为替身；它不构成模型质量证据。真实模型另由上述脚本、真实 worker 的孔洞测试和双窗口尺寸的对象点击/组合测试覆盖。凭据测试可能因 Windows 沙箱无法访问用户 Credential Store 跳过，模型测试不应因此被跳过。

## 当前局限与下一步

1. 复杂发丝、细树枝、玻璃/烟雾和严重遮挡仍不稳定；本轮不是专用 matting，也没有安装 SAM 2/3。先收集典型失败照片，再据此验收更强后端和高分辨率局部修正。
2. 网络有内部低分辨率特征/蒙版；服务最长边 1600px，原图导出放大蒙版不会恢复缺失细节。大图像素级精修应另做局部高分辨率管线。
3. 最多 6 个正负点；“退一点”可释放点数，“新目标”重新开始。多实例优先从元素清单分别分割后组合；一次点选不保证同时理解所有同类物体。
4. 取消会丢弃结果，但不能中途打断一次 ONNX 推理。首次无缓存需等待；推理独立于 GUI，画布导航仍可用。
5. 新打开项目保留已保存蒙版/草稿和语义清单，当前会话的编码与对象命中缓存不持久化。重开后第一次对象操作重新计算。
6. 自动定位也可能出错；可用 S 直接点选或框选后重新贴边，减少对云端文字定位的依赖。低置信提示是检查提醒，不是校准过的可靠性评估。

## 复现命令

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src --select F
.venv\Scripts\python.exe -m ruff format --check src
.venv\Scripts\python.exe scripts/qa_pixel_selection.py --variant s
.venv\Scripts\python.exe scripts/qa_segmentation_geometry.py --variant s
.venv\Scripts\python.exe scripts/qa_pixel_ui.py
```

真实千问复测必须显式加 `--live`；不能把普通自动测试变为个人账号收费调用。

最终回归：`180 passed, 1 skipped`，266.51 秒。跳过项为 Windows Credential Store；在正常用户会话单独补跑 `test_real_windows_vault_roundtrip`，`1 passed`，2.57 秒（pytest 缓存目录权限警告不影响测试结果）。因此 181 个不同测试均已通过。Ruff 未定义/未使用检查、36 个源文件格式检查和 `pip check` 均通过。新增的 22 项选区协议/约束/UI 路由测试包含在总数中，不能单独叠加计数。
