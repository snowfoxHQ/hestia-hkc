"""
观测知识库"同义不同形"近重复率 —— 语义去重第 2 档(docs/semantic-dedup-design.md §5)的前置观测。

思路:读 /knowledge/graph 的全部 KU,按类型分别统计"字面不同但高度相似"的疑似近重复对
      (即第 1 档 canonical fingerprint 抓不到、但语义/词面上很像的)。占比可观才值得做第 2 档。

向量来源(自动选择,优先真语义):
  1) sentence-transformers 多语模型 → 真语义向量(能抓"价值投资/价值型投资"这类同义)。
     没装则自动退回 ↓
  2) 字符 n-gram TF-IDF → 词面相似代理(零额外依赖;能抓"同一事实换措辞重述"这类高词面重叠,
     但抓不到纯同义词)。会在输出里注明用的是哪种。

用法:
  python scripts/observe_dedup.py                       # 默认连本机 8000,阈值 0.90
  python scripts/observe_dedup.py --url http://127.0.0.1:8000 --threshold 0.92 --cap 2000
"""
import sys, json, argparse, urllib.request, unicodedata
from collections import defaultdict


def fetch_kus(url):
    u = url.rstrip("/") + "/knowledge/graph?limit=5000"
    with urllib.request.urlopen(u, timeout=30) as r:
        return json.load(r)["kus"]


def ku_text(k):
    ex = k.get("extra") or {}
    t = k["ku_type"]
    if t in ("Fact", "Claim"):
        return (ex.get("statement") or k.get("summary") or k.get("name", "")).strip()
    if t == "Concept":
        return (k.get("name", "") + " " + (ex.get("definition") or "")).strip()
    if t == "Entity":
        return (k.get("name", "") + " " + " ".join(ex.get("aliases") or [])).strip()
    return k.get("name", "").strip()


def _norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    return "".join(ch for ch in s if unicodedata.category(ch)[0] in ("L", "N"))


def build_vectors(texts):
    """返回 (行归一化向量矩阵, 方法说明字符串)。"""
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        v = model.encode(texts, normalize_embeddings=True, show_progress_bar=False,
                         batch_size=64, convert_to_numpy=True)
        return np.asarray(v, dtype="float32"), "sentence-transformers 真语义向量"
    except Exception:
        pass
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import normalize as l2norm
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), min_df=1)
        X = vec.fit_transform(texts)
        return l2norm(X), "字符 n-gram TF-IDF 词面代理(非真语义;装 sentence-transformers 可升级)"
    except Exception:
        pass
    # 纯 numpy 兜底:字符 3-gram 哈希到定长向量 + L2 归一 → 余弦。零额外依赖,总能跑。
    import numpy as np, zlib
    D = 4096
    M = np.zeros((len(texts), D), dtype="float32")
    for i, t in enumerate(texts):
        s = _norm(t)
        grams = [s[j:j + 3] for j in range(len(s) - 2)] or ([s] if s else [])
        for g in grams:
            M[i, zlib.crc32(g.encode("utf-8")) % D] += 1.0
    norms = np.linalg.norm(M, axis=1, keepdims=True); norms[norms == 0] = 1.0
    return M / norms, "numpy 字符3-gram 哈希向量(词面代理,零依赖;装 sentence-transformers 可升级为真语义)"


def count_near_dups(vectors, threshold):
    """行归一化后,余弦 = 点积。返回 (高相似对数, 涉及的去重后节点数, 样例对索引)。"""
    import numpy as np
    n = vectors.shape[0]
    pairs = 0
    involved = set()
    examples = []
    dense = hasattr(vectors, "dot") and not hasattr(vectors, "toarray")
    for i in range(n):
        row = vectors[i]
        if hasattr(vectors, "toarray"):          # 稀疏(TF-IDF)
            sims = (vectors @ row.T).toarray().ravel()
        else:                                     # 稠密(语义)
            sims = vectors @ row
        for j in range(i + 1, n):
            if sims[j] >= threshold:
                pairs += 1
                involved.add(i); involved.add(j)
                if len(examples) < 6:
                    examples.append((i, j, float(sims[j])))
    return pairs, len(involved), examples


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--threshold", type=float, default=None,
                    help="余弦阈值;不填则按相似度方法自动选(语义 0.88 / 词面代理 0.62)")
    ap.add_argument("--cap", type=int, default=2000, help="每类型最多取多少个(防 O(n^2) 爆)")
    args = ap.parse_args()

    try:
        kus = fetch_kus(args.url)
    except Exception as e:
        print(f"拉取 {args.url}/knowledge/graph 失败: {e}"); sys.exit(1)
    if not kus:
        print("知识库为空 —— 请先摄入书籍再观测。"); return

    by_type = defaultdict(list)
    for k in kus:
        by_type[k["ku_type"]].append(k)

    print(f"总 KU: {len(kus)}")
    method_printed = False
    th = args.threshold
    grand = 0
    for t in ("Claim", "Fact", "Concept", "Entity"):
        items = by_type.get(t, [])
        if len(items) < 2:
            continue
        capped = items[: args.cap]
        texts = [ku_text(k) for k in capped]
        vectors, method = build_vectors(texts)
        if not method_printed:
            if th is None:                       # 按方法自动选阈值
                th = 0.88 if "语义" in method else 0.62
            print(f"相似度方法: {method}")
            print(f"阈值 cos ≥ {th}\n")
            method_printed = True
        pairs, involved, ex = count_near_dups(vectors, th)
        rate = involved / len(capped) * 100
        grand += pairs
        note = f"(取样前 {len(capped)}/{len(items)})" if len(items) > args.cap else ""
        print(f"── {t}: {len(items)} 个 {note}")
        print(f"   疑似近重复对: {pairs} | 涉及节点: {involved} (≈{rate:.1f}% 可能是近重复)")
        for i, j, s in ex[:3]:
            print(f"     [{s:.3f}] {texts[i][:38]!r}")
            print(f"            ↔ {texts[j][:38]!r}")
        print()

    print(f"合计疑似近重复对: {grand}")
    print("判读:某类型涉及节点占比 >10% 且样例确为同义/重述 → 值得做语义去重第 2 档;"
          "很少或样例其实是不同知识 → 维持现状,避免误合。")


if __name__ == "__main__":
    main()
