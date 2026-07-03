"""
hkc-ace / ace.py
Ability Compiler Engine

职责：
1. 预定义 Skill Taxonomy（不允许 LLM 自由生成 Skill）
2. 计算每个 Skill 的知识覆盖率
3. 达到门槛后编译输出 .hkap 能力包
4. Agent 直接 load_ability() 调用
"""
import sys, os

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from hkc_core.graph.base import GraphStore
from hkc_core.models.ku import AbilityKU, KU
from hkc_core.models.enums import KUType
from hkc_core.utils.id_gen import IDGenerator
from hkc_kep.event_bus import EventBus, KEPEvents


# ── Skill Taxonomy（从 JSON 配置加载，不再硬编码）──────────────
#
# 每个 Ability 的字段：
#   display_name      : 显示名（可中文）
#   domain            : 主领域（AbilityKU / 能力包的展示领域）
#   domains           : (可选) 参与覆盖匹配的领域列表；缺省则等于 [domain]。
#                       中文抽取常把一个主题拆成多个细碎领域（如 公文写作 / 写作方法论 /
#                       写作技巧），用 domains 把它们并起来一起算覆盖。
#   required_skills   : 必须覆盖的 Skill 列表
#   optional_skills   : 加分项
#   min_coverage      : required_skills 中至少多少比例的 skill 覆盖度 >= 0.5
#   concept_checklist : 每个 Skill 的核心概念列表（Coverage 计算基准，中 / 英文皆可）
#   workflows         : 技能执行顺序
#
# 配置文件默认 = 本包目录下 skill_taxonomy.json；可用环境变量 HKC_ACE_TAXONOMY
# 指向自定义文件（开源用户可替换成自己的能力定义，无需改代码）。


def _load_taxonomy() -> dict:
    path = os.environ.get("HKC_ACE_TAXONOMY") or str(
        Path(__file__).parent / "skill_taxonomy.json"
    )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


SKILL_TAXONOMY: dict[str, dict] = _load_taxonomy()


def _ability_domains(taxonomy_entry: dict) -> list[str]:
    """能力参与覆盖匹配的领域列表：优先 domains（多领域），否则回退单个 domain。"""
    ds = taxonomy_entry.get("domains")
    if ds:
        return list(ds)
    d = taxonomy_entry.get("domain")
    return [d] if d else []


# ── Skill 数据结构 ────────────────────────────────────────────

@dataclass
class Skill:
    skill_id:     str
    skill_key:    str
    display_name: str
    domain:       str
    ku_refs:      list[str]   = field(default_factory=list)
    coverage:     float       = 0.0
    concept_hits: list[str]   = field(default_factory=list)


@dataclass
class Workflow:
    name:  str
    steps: list[str]


@dataclass
class AbilityPackage:
    ability_key:   str
    display_name:  str
    domain:        str
    skills:        list[Skill]
    workflows:     list[Workflow]
    coverage:      dict[str, float]
    knowledge_refs: list[str]
    compiled_at:   str
    version:       str = "1.0.0"

    def to_dict(self) -> dict:
        return {
            "hkap_version": "1.0",
            "ability_key":  self.ability_key,
            "display_name": self.display_name,
            "domain":       self.domain,
            "compiled_at":  self.compiled_at,
            "version":      self.version,
            "coverage":     self.coverage,
            "skills": [
                {
                    "skill_key":   s.skill_key,
                    "display_name": s.display_name,
                    "ku_refs":     s.ku_refs,
                    "coverage":    s.coverage,
                    "concept_hits": s.concept_hits,
                }
                for s in self.skills
            ],
            "workflows": [
                {"name": w.name, "steps": w.steps}
                for w in self.workflows
            ],
            "knowledge_summary": (
                f"基于 {len(self.knowledge_refs)} 个 KU 编译"
            ),
        }

    def get_skill_context(self, skill_key: str) -> dict:
        """Agent 调用：获取某个 Skill 的知识上下文。"""
        for s in self.skills:
            if s.skill_key == skill_key:
                return {
                    "skill_key":  skill_key,
                    "ku_refs":    s.ku_refs,
                    "coverage":   s.coverage,
                    "concept_hits": s.concept_hits,
                }
        return {}


# ── Coverage 计算器 ───────────────────────────────────────────

class CoverageCalculator:
    """
    计算每个 Skill 的知识覆盖率。
    基于 Concept Checklist：
      coverage = 被 KU 覆盖的核心概念数 / 总核心概念数
    """

    def calculate(
        self,
        ability_key: str,
        ku_pool: list[KU],
    ) -> dict[str, float]:
        taxonomy  = SKILL_TAXONOMY[ability_key]
        checklist = taxonomy.get("concept_checklist", {})
        coverage  = {}

        for skill_key in taxonomy["required_skills"]:
            concepts   = checklist.get(skill_key, [])
            if not concepts:
                coverage[skill_key] = 0.0
                continue
            hit_count  = sum(
                1 for concept in concepts
                if self._concept_covered(concept, ku_pool)
            )
            coverage[skill_key] = round(hit_count / len(concepts), 2)

        for skill_key in taxonomy.get("optional_skills", []):
            concepts = checklist.get(skill_key, [])
            if concepts:
                hit_count = sum(
                    1 for c in concepts
                    if self._concept_covered(c, ku_pool)
                )
                coverage[skill_key] = round(hit_count / len(concepts), 2)

        return coverage

    def _concept_covered(self, concept: str, ku_pool: list[KU]) -> bool:
        """
        判断某个核心概念是否被 KU 池覆盖。
        v1：关键词匹配（name + summary + tags）
        v2：向量相似度匹配
        """
        concept_lower = concept.lower()
        for ku in ku_pool:
            text = f"{ku.name} {ku.summary} {' '.join(ku.tags)}".lower()
            if concept_lower in text:
                return True
            # 支持中文关键词映射
            if self._cn_match(concept_lower, text):
                return True
        return False

    _CN_MAP = {
        "momentum":            ["动量", "趋势", "momentum"],
        "value factor":        ["价值因子", "价值", "低估"],
        "drawdown":            ["回撤", "最大回撤"],
        "sharpe ratio":        ["夏普", "sharpe"],
        "diversification":     ["分散", "多元", "分散化"],
        "moat":                ["护城河"],
        "intrinsic value":     ["内在价值", "内生价值"],
        "loss aversion":       ["损失厌恶", "亏损"],
        "confirmation bias":   ["确认偏差", "确认偏见"],
        "attention":           ["注意力", "attention"],
        "transformer":         ["transformer", "变换器"],
    }

    def _cn_match(self, concept: str, text: str) -> bool:
        aliases = self._CN_MAP.get(concept, [])
        return any(a in text for a in aliases)

    def can_compile(
        self,
        ability_key: str,
        coverage: dict[str, float],
    ) -> bool:
        """
        检查是否达到编译门槛：
        required_skills 中，coverage >= 0.5 的比例 >= min_coverage
        """
        taxonomy = SKILL_TAXONOMY[ability_key]
        required = taxonomy["required_skills"]
        passed   = sum(
            1 for s in required
            if coverage.get(s, 0.0) >= 0.5
        )
        return (passed / len(required)) >= taxonomy["min_coverage"]


# ── ACE 主引擎 ───────────────────────────────────────────────

class AbilityCompilerEngine:

    def __init__(
        self,
        graph_store:   GraphStore,
        event_bus:     EventBus,
        id_gen:        IDGenerator,
        output_dir:    str = "abilities",
    ):
        self.store      = graph_store
        self.bus        = event_bus
        self.id_gen     = id_gen
        self.calculator = CoverageCalculator()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _gather_ku_pool(self, taxonomy: dict, limit: int = 500) -> list[KU]:
        """跨该能力的所有领域收集 KU 池并去重（中文细碎领域场景必需）。"""
        seen: dict[str, KU] = {}
        for d in _ability_domains(taxonomy):
            for ku in self.store.query_by_domain(d, limit=limit):
                seen[ku.ku_id] = ku
        return list(seen.values())

    def compile(self, ability_key: str) -> Optional[AbilityPackage]:
        """
        编译一个 Ability Package。
        1. 从 GraphStore 获取相关领域 KU
        2. 计算覆盖率
        3. 检查门槛
        4. 组装 Skills + Workflows
        5. 写出 .hkap 文件
        6. 写入 GraphStore（AbilityKU）
        7. 发布事件
        """
        if ability_key not in SKILL_TAXONOMY:
            raise ValueError(f"Unknown ability: {ability_key}")

        taxonomy = SKILL_TAXONOMY[ability_key]
        domain   = taxonomy["domain"]

        # 1. 获取 KU 池（跨该能力的多个领域并集，中文细碎领域场景必需）
        ku_pool = self._gather_ku_pool(taxonomy, limit=500)
        if not ku_pool:
            return None

        # 2. 计算覆盖率
        coverage = self.calculator.calculate(ability_key, ku_pool)

        # 3. 检查门槛
        if not self.calculator.can_compile(ability_key, coverage):
            return None

        # 4. 组装 Skills
        skills = self._build_skills(ability_key, ku_pool, coverage)

        # 5. 组装 Workflows
        workflows = [
            Workflow(name=w["name"], steps=w["steps"])
            for w in taxonomy["workflows"]
        ]

        # 6. 打包
        now = datetime.now(timezone.utc).isoformat()
        package = AbilityPackage(
            ability_key    = ability_key,
            display_name   = taxonomy["display_name"],
            domain         = domain,
            skills         = skills,
            workflows      = workflows,
            coverage       = coverage,
            knowledge_refs = [ku.ku_id for ku in ku_pool],
            compiled_at    = now,
        )

        # 7. 确定 .hkap 路径（先定义，写 store 时引用，最后才写文件）
        hkap_path = self.output_dir / f"{ability_key}.hkap"

        # 8. 写入 GraphStore（先于文件，store 失败可回滚，文件失败可重新编译覆盖）
        ku_id = self.id_gen.next("Ability")
        ability_ku = AbilityKU(
            ku_id        = ku_id,
            name         = taxonomy["display_name"],
            summary      = f"ACE 编译产物，基于 {len(ku_pool)} 个 KU",
            domain       = domain,
            ability_key  = ability_key,
            skills       = [s.skill_id for s in skills],
            coverage     = coverage,
            knowledge_refs = package.knowledge_refs,
            package_path = str(hkap_path),
            pkg_version  = package.version,
        )
        self.store.put(ability_ku)

        # 9. 写出 .hkap 文件
        hkap_path = self.output_dir / f"{ability_key}.hkap"
        hkap_path.write_text(
            json.dumps(package.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # 10. 发布事件
        self.bus.publish({
            "event":  KEPEvents.ABILITY_CREATED,
            "source": "ACE",
            "payload": {
                "ability_key": ability_key,
                "ku_id":       ku_id,
                "coverage":    coverage,
                "hkap_path":   str(hkap_path),
            }
        })

        return package

    def _build_skills(
        self,
        ability_key: str,
        ku_pool: list[KU],
        coverage: dict[str, float],
    ) -> list[Skill]:
        taxonomy  = SKILL_TAXONOMY[ability_key]
        checklist = taxonomy.get("concept_checklist", {})
        skills    = []

        all_skill_keys = (
            taxonomy["required_skills"] +
            taxonomy.get("optional_skills", [])
        )

        for skill_key in all_skill_keys:
            cov = coverage.get(skill_key, 0.0)
            if cov == 0.0:
                continue

            # 找到覆盖该 Skill 的 KU
            concepts  = checklist.get(skill_key, [])
            skill_kus = [
                ku for ku in ku_pool
                if self.calculator._concept_covered(
                    skill_key.replace("_", " "), [ku]
                ) or any(
                    self.calculator._concept_covered(c, [ku])
                    for c in concepts
                )
            ]

            # 命中的核心概念
            hit_concepts = [
                c for c in concepts
                if self.calculator._concept_covered(c, ku_pool)
            ]

            skill = Skill(
                skill_id     = self.id_gen.next("Skill"),
                skill_key    = skill_key,
                display_name = skill_key.replace("_", " ").title(),
                domain       = taxonomy["domain"],
                ku_refs      = [ku.ku_id for ku in skill_kus[:20]],
                coverage     = cov,
                concept_hits = hit_concepts,
            )
            skills.append(skill)

        return skills

    def load_ability(self, ability_key: str) -> Optional[AbilityPackage]:
        """从 .hkap 文件加载已编译的 Ability Package。"""
        hkap_path = self.output_dir / f"{ability_key}.hkap"
        if not hkap_path.exists():
            return None
        data = json.loads(hkap_path.read_text(encoding="utf-8"))
        skills = [
            Skill(
                skill_id     = f"SKL_{i:04d}",
                skill_key    = s["skill_key"],
                display_name = s["display_name"],
                domain       = data["domain"],
                ku_refs      = s.get("ku_refs", []),
                coverage     = s.get("coverage", 0.0),
                concept_hits = s.get("concept_hits", []),
            )
            for i, s in enumerate(data.get("skills", []))
        ]
        workflows = [
            Workflow(name=w["name"], steps=w["steps"])
            for w in data.get("workflows", [])
        ]
        return AbilityPackage(
            ability_key    = data["ability_key"],
            display_name   = data["display_name"],
            domain         = data["domain"],
            skills         = skills,
            workflows      = workflows,
            coverage       = data["coverage"],
            knowledge_refs = data.get("knowledge_refs", []),
            compiled_at    = data["compiled_at"],
            version        = data["version"],
        )

    def list_available(self) -> list[str]:
        """返回所有已定义的 Ability key 列表。"""
        return list(SKILL_TAXONOMY.keys())

    def coverage_report(self, ability_key: str) -> dict:
        """
        在不编译的情况下，查看当前知识库对某 Ability 的覆盖情况。
        用于判断"还差哪些知识"。
        """
        if ability_key not in SKILL_TAXONOMY:
            return {}
        taxonomy = SKILL_TAXONOMY[ability_key]
        ku_pool  = self._gather_ku_pool(taxonomy, limit=500)
        coverage = self.calculator.calculate(ability_key, ku_pool)
        can      = self.calculator.can_compile(ability_key, coverage)

        missing = [
            s for s in taxonomy["required_skills"]
            if coverage.get(s, 0.0) < 0.5
        ]
        return {
            "ability_key":   ability_key,
            "display_name":  taxonomy.get("display_name", ability_key),
            "domains":       _ability_domains(taxonomy),
            "can_compile":   can,
            "coverage":      coverage,
            "missing_skills": missing,
            "ku_count":      len(ku_pool),
        }
