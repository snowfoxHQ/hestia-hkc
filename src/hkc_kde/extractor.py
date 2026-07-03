"""
hkc-kde / extractor.py
知识提取器：把 Chunk 文本交给 LLM，提取结构化 KU。

核心规则：
- Fact  ：可被客观验证，confidence 固定 1.0
- Claim ：依赖理论或存在对立观点，给出 0.0-1.0 置信度
- Entity：真实存在的人 / 机构 / 产品
- Concept：抽象知识单元

v1：调用 Anthropic API（claude-sonnet-4-6）
v2：可替换为本地模型，接口不变
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .chunker import Chunk
from .models import RawExtraction, ExtractionResult

logger = logging.getLogger(__name__)


# ── System Prompt ─────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是知识提取专家。从用户提供的文本中提取结构化知识。

## 严格规则

**Fact vs Claim 必须区分：**
- Fact：可被客观验证、不随时间/理论变化的陈述（人物生卒年、历史事件、测量值）
  → confidence 固定 1.0，不需要估算
- Claim：依赖理论框架、存在对立观点、或需要解释的观点/结论
  → 给出 0.0-1.0 的置信度，0.5 表示不确定

**输出格式：严格 JSON，不要任何解释文字、不要 Markdown 代码块**

## 输出 Schema

{
  "facts": [
    {"statement": "陈述原文", "source_hint": "来源线索（可选）"}
  ],
  "claims": [
    {"statement": "观点陈述", "confidence": 0.0-1.0, "domain": "领域"}
  ],
  "entities": [
    {"name": "实体名", "type": "Person|Organization|Product|Event|Place", "aliases": []}
  ],
  "concepts": [
    {"name": "概念名", "domain": "领域", "definition": "简短定义"}
  ]
}

## 判断示例

Fact（✓）: "巴菲特生于1930年" / "GDP增长率为3.2%" / "该公司成立于1994年"
Claim（✓）: "价值投资长期跑赢市场" / "高脂饮食有益代谢健康" / "这项技术将颠覆行业"

每类最多提取 5 个最重要的条目。如果文本中没有某类知识，返回空列表。\
"""


class Extractor:

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 provider: str = "anthropic", base_url: Optional[str] = None):
        """
        provider: 'anthropic'(默认) | 'deepseek' | 'openai' | 'openai-compatible'
        - anthropic: 用 anthropic SDK,model 默认 claude-sonnet-4-6
        - deepseek : 用 openai SDK(兼容),base_url=https://api.deepseek.com,model 默认 deepseek-chat
        - openai / openai-compatible: 用 openai SDK,可自定义 base_url
        """
        self._apply_config(provider, api_key, model, base_url)
        self._client = None

    def _apply_config(self, provider, api_key, model, base_url):
        """应用 LLM 配置(init 和运行时 reconfigure 共用)。"""
        self.provider = (provider or "anthropic").lower()
        self.api_key  = api_key
        self.base_url = base_url
        # 各 provider 的默认 model
        if model:
            self.model = model
        elif self.provider == "deepseek":
            self.model = "deepseek-chat"
        elif self.provider in ("openai", "openai-compatible"):
            self.model = "gpt-4o-mini"
        else:
            self.model = "claude-sonnet-4-6"
        # deepseek 的默认 base_url
        if self.provider == "deepseek" and not self.base_url:
            self.base_url = "https://api.deepseek.com"

    def reconfigure(self, provider=None, api_key=None, model=None, base_url=None):
        """运行时重设 LLM 配置(界面配置用),清掉缓存的 client。"""
        self._apply_config(provider, api_key, model, base_url)
        self._client = None

    def config_summary(self) -> dict:
        """返回当前配置(api_key 脱敏),供界面展示。"""
        k = self.api_key or ""
        masked = (k[:6] + "…" + k[-4:]) if len(k) > 12 else ("已设置" if k else "")
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url or "",
            "api_key_masked": masked,
            "has_key": bool(k),
        }

    def _get_client(self):
        if self._client is not None:
            return self._client
        if self.provider == "anthropic":
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError("anthropic SDK 未安装，请运行: pip install anthropic")
        else:
            # deepseek / openai / openai-compatible 都用 openai SDK
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            except ImportError:
                raise ImportError("openai SDK 未安装，请运行: pip install openai")
        return self._client

    def extract(self, chunks: list[Chunk], progress_cb=None) -> ExtractionResult:
        """
        批量提取：每个 Chunk 单独调用 LLM，汇总为 ExtractionResult。

        progress_cb: 可选 (processed, total) -> None 回调，每处理完一个 chunk 触发一次，
                     供异步摄入上报真进度。
        """
        if not chunks:
            return ExtractionResult(doc_id="", source="", items=[])

        doc_id = chunks[0].doc_id
        source = doc_id  # ExtractionResult.source 存 doc_id，真实路径由 Packager 从外部传入
        items: list[RawExtraction] = []
        fail_count = 0
        total = len(chunks)

        # 并发抽取:每段一次 LLM 调用是 I/O 密集,串行 500 段要 40+ 分钟。用有限并发
        # (默认 5,可经 HKC_EXTRACT_CONCURRENCY 调)把整本书从几十分钟压到几分钟。
        # 并发度要温和,避免触发各 provider 的限流(429);进度回调在主线程串行调,无竞态。
        import os
        try:
            workers = max(1, int(os.getenv("HKC_EXTRACT_CONCURRENCY", "5")))
        except ValueError:
            workers = 5
        workers = min(workers, total)
        done = 0

        def _tick(raw):
            nonlocal fail_count, done
            if raw:
                items.append(raw)
            else:
                fail_count += 1
            done += 1
            logger.info("提取 Chunk %d/%d (doc=%s)", done, total, doc_id)
            if progress_cb is not None:
                try:
                    progress_cb(done, total)
                except Exception:
                    pass   # 进度上报失败不影响摄入本身

        if workers <= 1:
            for chunk in chunks:
                _tick(self._extract_chunk(chunk))
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract") as pool:
                futures = [pool.submit(self._extract_chunk, ch) for ch in chunks]
                for fut in as_completed(futures):
                    try:
                        raw = fut.result()
                    except Exception as e:
                        logger.error("Chunk 提取线程异常: %s", e)
                        raw = None
                    _tick(raw)

        # 全部 chunk 都抽取失败 → 很可能是 LLM 配置问题(没 key / key 错 / 网络不通),
        # 明确报错,避免"摄入成功但 0 知识"的静默失败让用户困惑。
        if fail_count == len(chunks) and len(chunks) > 0:
            logger.error(
                "知识抽取全部失败 (%d/%d chunks)。请检查 LLM 配置:provider=%s, model=%s, "
                "是否已正确填写 API Key。", fail_count, len(chunks), self.provider, self.model
            )

        return ExtractionResult(doc_id=doc_id, source=source, items=items)

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        """
        通用单轮补全（非抽取）：给定 system+user，返回模型文本。
        供「综合页 LLM 综述」等派生视图复用同一套 provider 配置，保持模型无关。
        与 _extract_chunk 一样兼容 anthropic 与 openai 系两条路径。
        """
        client = self._get_client()
        if self.provider == "anthropic":
            resp = client.messages.create(
                model=self.model, max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return resp.content[0].text.strip()
        resp = client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content.strip()

    def _extract_chunk(self, chunk: Chunk) -> Optional[RawExtraction]:
        """
        单个 Chunk 的提取。
        LLM 调用失败时记录日志并返回 None，不中断整个流程。
        """
        try:
            client = self._get_client()
            if self.provider == "anthropic":
                resp = client.messages.create(
                    model      = self.model,
                    max_tokens = 2048,
                    system     = _SYSTEM_PROMPT,
                    messages   = [{"role": "user", "content": chunk.content}],
                )
                raw_text = resp.content[0].text.strip()
            else:
                # OpenAI 兼容格式(DeepSeek / OpenAI):system 作为一条 message
                resp = client.chat.completions.create(
                    model       = self.model,
                    max_tokens  = 2048,
                    messages    = [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": chunk.content},
                    ],
                )
                raw_text = resp.choices[0].message.content.strip()
            raw = self._parse_response(raw_text, chunk.source_hint)
            if raw:
                # 把本 chunk 的原文段落挂到每条抽取结果上,供 packager 存进 KU,
                # 前端点击知识节点时可显示"原文段落"(见 KU.source_text)。
                src = chunk.content
                for d in (raw.facts + raw.claims + raw.concepts + raw.entities):
                    d["_src"] = src
            return raw

        except Exception as e:
            logger.error("Chunk 提取失败 (chunk_id=%s): %s", chunk.chunk_id, e)
            return None

    def _parse_response(self, text: str, source_hint: str) -> Optional[RawExtraction]:
        """
        解析 LLM 返回的 JSON。
        容错处理：去掉可能的 Markdown 代码块包裹。
        """
        # 去掉 ```json ... ``` 包裹（LLM 偶尔忽略指令）
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\s*```$',          '', text, flags=re.MULTILINE)
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("JSON 解析失败: %s\n原文: %.200s", e, text)
            return None

        # 校验并规范化
        facts    = self._validate_facts(data.get("facts", []))
        claims   = self._validate_claims(data.get("claims", []))
        entities = self._validate_entities(data.get("entities", []))
        concepts = self._validate_concepts(data.get("concepts", []))

        if not any([facts, claims, entities, concepts]):
            return None

        return RawExtraction(
            facts       = facts,
            claims      = claims,
            entities    = entities,
            concepts    = concepts,
            source_hint = source_hint,
        )

    # ── 校验方法：确保字段存在且类型正确 ────────────────────

    def _validate_facts(self, items: list) -> list[dict]:
        result = []
        for item in items[:5]:
            if not isinstance(item, dict):
                continue
            stmt = str(item.get("statement", "")).strip()
            if stmt:
                result.append({
                    "statement":   stmt,
                    "source_hint": str(item.get("source_hint", "")),
                })
        return result

    def _validate_claims(self, items: list) -> list[dict]:
        result = []
        for item in items[:5]:
            if not isinstance(item, dict):
                continue
            stmt = str(item.get("statement", "")).strip()
            if not stmt:
                continue
            conf = item.get("confidence", 0.5)
            try:
                conf = float(conf)
                conf = max(0.0, min(1.0, conf))
            except (TypeError, ValueError):
                conf = 0.5
            result.append({
                "statement":  stmt,
                "confidence": conf,
                "domain":     str(item.get("domain", "")),
            })
        return result

    def _validate_entities(self, items: list) -> list[dict]:
        valid_types = {"Person", "Organization", "Product", "Event", "Place"}
        result = []
        for item in items[:5]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            entity_type = item.get("type", "Person")
            if entity_type not in valid_types:
                entity_type = "Person"
            result.append({
                "name":    name,
                "type":    entity_type,
                "aliases": list(item.get("aliases", [])),
            })
        return result

    def _validate_concepts(self, items: list) -> list[dict]:
        result = []
        for item in items[:5]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            result.append({
                "name":       name,
                "domain":     str(item.get("domain", "")),
                "definition": str(item.get("definition", "")),
            })
        return result
