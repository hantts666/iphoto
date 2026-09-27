# doc

选区操作面板重构专项文档：

1. [selection-analysis.md](selection-analysis.md) — 现状分析：面板功能清单、前后端代码分布（含文件:行号）、"操作迷幻"的 9 个问题诊断（P1-P9）、测试基线。
2. [selection-redesign.md](selection-redesign.md) — 设计方案：Python 端 `SelectionController` 公共门面 + QML 共享组件抽取、"做出选区→修边→输出"三步操作重组、分 4 步实施计划与验收标准。
