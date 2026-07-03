# HKC 知识星球

AI 原生知识系统:摄入文本/文件 → LLM 抽取结构化知识 → KEE 演化去重 → 3D 知识星球可视化 + 能力编译 + AI 综述。**模型无关**,任意 LLM(DeepSeek/Claude/OpenAI/本地)皆可接入。

## 🚀 一键启动(推荐)

**macOS / Linux:**
```bash
cd hestia-hkc
./start.sh
```

**Windows:** 双击 `start.bat`(或在 PowerShell / CMD 运行)

模型配置推荐启动后在界面「连接设置」里填(见下方说明),不用记环境变量。

脚本会自动:安装依赖 → 起后端 → 起前端 → 打开浏览器。
按 `Ctrl+C`(Windows 关窗口)即停止全部。

启动后浏览器会打开界面,点左下「连接设置」→ 填 `http://localhost:8000` → 连接。

## 配置知识抽取模型(摄入知识必需)

摄入知识时,系统用 LLM 从文本/文件里抽取实体、概念、观点。支持 **DeepSeek / Claude / OpenAI / 本地模型**。

### 方式一:界面配置(推荐,最简单)

启动后,在界面左下点「连接设置」,里面可以直接:
- 选择模型提供商(DeepSeek / Claude / OpenAI / 自定义本地)
- 填 API Key、模型名称(留空用默认)
- 自定义/本地模型可填 API 地址(如 Ollama)

填好点「连接」即可,**不用设置任何环境变量**。这是推荐方式。

### 方式二:启动前用环境变量(可选)

如果想启动时就配好,按你的系统设置环境变量:

**macOS / Linux:**
```bash
export HKC_LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=sk-你的key
./start.sh
```

**Windows PowerShell:**
```powershell
$env:HKC_LLM_PROVIDER="deepseek"
$env:DEEPSEEK_API_KEY="sk-你的key"
.\start.bat
```

**Windows CMD:**
```cmd
set HKC_LLM_PROVIDER=deepseek
set DEEPSEEK_API_KEY=sk-你的key
start.bat
```

不配置也能启动并浏览 3D 界面(演示数据),但「摄入知识」需要先配好模型。

## 手动启动(不用脚本)

```bash
# 1. 建虚拟环境并激活(Python ≥ 3.11)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. 装依赖
pip install -e ".[all]"

# 3. 配置知识抽取模型 —— 推荐【跳过这步】，启动后在界面「连接设置」里图形化填(最省事)。
#    如需用环境变量，按你的终端选对应写法(下面以推荐的 DeepSeek 为例)：
#      Linux/macOS (bash) :  export HKC_LLM_PROVIDER=deepseek; export DEEPSEEK_API_KEY=sk-...
#      Windows PowerShell :  $env:HKC_LLM_PROVIDER="deepseek"; $env:DEEPSEEK_API_KEY="sk-..."
#      Windows CMD        :  set HKC_LLM_PROVIDER=deepseek && set DEEPSEEK_API_KEY=sk-...
#    换其它 provider：把 key 变量名换成对应的 —— Claude=ANTHROPIC_API_KEY、OpenAI=OPENAI_API_KEY、
#    本地模型 provider=openai-compatible + HKC_LLM_BASE_URL + HKC_LLM_MODEL。详见下方「配置」表。

# 4. 起后端(激活 venv 后统一用 python -m uvicorn,不依赖 PATH)
python -m uvicorn hkc_api.main:app --host 127.0.0.1 --port 8000

# 5. 另开终端起前端
cd hkc-ui && python -m http.server 8080
# 浏览器打开 http://localhost:8080/index.html
```

> `python -m uvicorn` / `python -m http.server` 在三种终端下写法一致；激活 venv 后 `python` 即指向 venv 内解释器，不必写全路径。

## 配置(环境变量)

| 变量 | 默认 | 说明 |
|------|------|------|
| `HKC_LLM_PROVIDER` | `anthropic` | LLM 提供商:`anthropic` / `deepseek` / `openai` / `openai-compatible` |
| `DEEPSEEK_API_KEY` | (无) | DeepSeek key(provider=deepseek 时) |
| `ANTHROPIC_API_KEY` | (无) | Claude key(provider=anthropic 时) |
| `OPENAI_API_KEY` | (无) | OpenAI key(provider=openai 时) |
| `HKC_LLM_MODEL` | 按provider | 模型名(留空用默认:deepseek-chat / claude-sonnet-4-6) |
| `HKC_LLM_BASE_URL` | 按provider | 自定义 API 地址(本地模型/代理时) |
| `HKC_EMBEDDING` | `stub` | 语义向量:`stub`(默认,快/无语义) / `local`(真实,需 sentence-transformers) / `tei`(远程)。一键脚本 `start.*` 默认设为 `local` |
| `HKC_DATA_DIR` | `./hkc_data` | 知识库数据目录 |
| `HKC_PORT` | `8000` | 后端端口 |
| `HKC_UI_PORT` | `8080` | 前端端口 |

## 界面操作

- **3D 星球**:拖拽转视角 · 滚轮缩放 · 点节点飞入看详情 · hover 放大
- **摄入知识**:左栏「摄入知识」→ 贴文本 或 上传 `.pdf/.md/.txt/.epub/.mobi/.azw3`;大文件走**异步摄入**(秒返回、后台跑、进度可见,期间可继续上传)
- **综合页 / AI 综述**:点节点右栏即时组装 wiki 式词条;点「✦ 生成 AI 综述」用 LLM 把邻域写成连贯词条(按需触发、结果缓存)
- **搜索**:左栏搜索框,切换 混合/关键词/语义/图谱 模式
- **能力编译(ACE)**:左栏「能力编译」把稳定知识编译成能力包(内置中/英文能力,可配置)
- **冲突裁决**:有矛盾观点时左栏出现,可裁决采纳

## 注意

3D 界面用 Three.js (ESM),**必须通过 http 访问**(脚本已处理),不能直接双击 index.html。
所有依赖本地离线(Three.js 在 vendor/),无需外网——除了摄入/生成综述时调所配置的 LLM API(本地模型如 Ollama 则完全离线)。
