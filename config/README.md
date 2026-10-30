# 配置说明

当前问答原型从进程环境和根目录`.env.local`读取配置，进程环境优先。复制根目录`.env.example`后仅在本机填写密钥。`default.yaml`为后续应用配置草案，当前原型不会加载它。

| 配置 | 作用 | 默认/示例 |
| --- | --- | --- |
| `LLM_BASE_URL` | 兼容对话接口地址 | 模板为`https://api.deepseek.com`，脚本无配置时为OpenRouter |
| `LLM_MODEL` | 对话模型ID | 必填，模板为`deepseek-flash` |
| `LLM_API_KEY_ENV` | 密钥环境变量名 | 模板为`DEEPSEEK_API_KEY` |
| `DEEPSEEK_API_KEY` | 本地真实密钥 | 模板留空，由使用者本机填写 |
| `LLM_MAX_TOKENS` | 单次输出预算 | `2048` |
| `LLM_THINKING` | DeepSeek官方思考开关 | `disabled`，只向官方域名发送 |
| `ONNX_DML_DEVICE_ID` | DirectML的DXGI适配器编号 | `0` |
| `EMBEDDING_MODEL` | 本地或在线向量模型ID | 本地默认`Xenova/bge-small-zh-v1.5` |
| `EMBEDDING_BASE_URL` | 在线向量接口地址 | 仅`--embedding-backend api`使用 |
| `EMBEDDING_API_KEY_ENV` | 在线向量密钥变量名 | 默认`OPENROUTER_API_KEY`，与对话密钥独立 |

命令行参数：`--document`指定资料，`--device dml/cpu/auto`选择执行后端，`--retrieval-only`只检索，`--env-file`指定配置文件，`--cache-dir`指定FAISS索引目录。

缓存相对于启动目录：`work/model_cache/`为固定版本ONNX模型，`work/rag_cache/`为索引及原文元数据。启动脚本先切到仓库根目录。缓存与密钥已被`.gitignore`排除；不要将模型二进制、真实业务资料或原文索引提交GitHub。
