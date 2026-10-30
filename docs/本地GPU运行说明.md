# 阶段1：本地GPU混合检索问答

本阶段是单文件Python命令行原型，不涉及Web。业务为赛事补给与站点协同。

## 实现范围

本地Markdown → 每500字符切片 → 中文BGE向量编码 → FAISS持久化；提问后通过BM25与向量检索，以RRF融合取top3，再调用可替换供应商的对话API。核心函数均有中文注释并标记后续可复用。

Windows使用ONNX Runtime DirectML执行GPU向量编码，FAISS在CPU检索。回答仍由DeepSeek API生成，不在独显上部署完整DeepSeek大模型。`--device dml`要求GPU后端，初始化失败会报错；`--device cpu`可显式使用CPU。

## 安装与运行

在项目根目录执行（PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt
Copy-Item .env.example .env.local
# 编辑.env.local填写自己的DEEPSEEK_API_KEY，然后运行：
.\.venv\Scripts\python.exe src/race_supply/phase1_rag.py --document docs/discovery-report.md --device dml
```

不配置对话密钥也可以验证GPU检索：

```powershell
.\.venv\Scripts\python.exe src/race_supply/phase1_rag.py --document docs/discovery-report.md --device dml --retrieval-only --question "签收数量不一致时如何处理？"
.\.venv\Scripts\python.exe src/race_supply/phase1_rag.py --self-test
```

首次运行从Hugging Face下载固定修订的`Xenova/bge-small-zh-v1.5` ONNX模型，缓存至`work/model_cache`；来源是BAAI中文BGE模型的ONNX转换版。FAISS缓存至`work/rag_cache`，源文、模型或切片参数变化会重建。模型、索引、虚拟环境与密钥均不上传GitHub。

`ONNX_DML_DEVICE_ID`是DXGI适配器编号，不应直接用CUDA编号替代。此次电脑验证的0号DXGI适配器为NVIDIA RTX 4060 Laptop GPU（8GB）。

## 2026-10-30验证结果与边界

- 本地访谈报告按500字符分为45片，完成真实向量建库及缓存复用。
- GPU运行时性能记录显示125次DirectML算子事件、2次CPU算子事件，证实GPU实际参与执行。
- BM25与向量混合检索top3后，成功调用DeepSeek官方`deepseek-flash`生成中文回答。
- 离线自检通过：切片、中文检索、FAISS保存读取、HTTP适配、无效引用拦截。
- `discovery-report.md`是合成场景访谈资料，不能当成已批准的赛事制度；测试回答明确指出签收差异处置步骤仍缺正式依据。

单次链路成功不代表答案质量或性能已完成系统评测；编号引用校验也不等于事实支持校验。后续仍需使用正式资料建立问答评测集。

模型配置见脚本头部和`.env.example`。对话API可能计费；本地向量编码无需向量API密钥。本次测试密钥仅用于临时进程，没有写入项目文件。

参考：[BGE模型说明](https://huggingface.co/BAAI/bge-small-zh-v1.5)、[ONNX转换模型](https://huggingface.co/Xenova/bge-small-zh-v1.5)、[DirectML运行说明](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)、[DeepSeek思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。
