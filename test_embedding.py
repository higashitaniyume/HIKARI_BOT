"""测试本地嵌入模型 —— sentence-transformers。"""

import time
import math


def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


if __name__ == "__main__":
    print("=" * 55)
    print("Local Embedding Model Test")
    print("=" * 55)

    # 1. Load model
    print("\n1. Loading paraphrase-multilingual-MiniLM-L12-v2 ...")
    start = time.monotonic()
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    elapsed = time.monotonic() - start
    dim = model.get_embedding_dimension()
    print(f"   OK, loaded in {elapsed:.1f}s, dim={dim}")

    # 2. Basic embedding
    print("\n2. Basic embedding test")
    docs = [
        "今天天气真好，适合出去玩",
        "外面阳光明媚，是郊游的好日子",
        "Python的asyncio库用于异步编程",
        "如何修复代码中的race condition",
        "我喜欢吃披萨和意大利面",
        "晚餐吃什么好呢",
        "永雏塔菲是一个虚拟主播",
        "B站上有很多有趣的虚拟主播",
        "服务器的CPU使用率突然飙升到100%",
        "数据库连接池耗尽了，需要排查",
    ]
    start = time.monotonic()
    vecs = model.encode(docs, normalize_embeddings=True)
    elapsed = time.monotonic() - start
    print(f"   {len(docs)} docs -> {vecs.shape[1]}d vectors in {elapsed:.2f}s")
    print(f"   Sample (first 5): {[f'{v:.4f}' for v in vecs[0][:5].tolist()]}")

    # 3. Semantic similarity
    print("\n3. Semantic similarity test")
    query = "今天适合出去郊游踏青"
    print(f"   Query: {query}")
    qv = model.encode([query], normalize_embeddings=True)[0]

    results = []
    for text, vec in zip(docs, vecs):
        sim = cosine_similarity(qv.tolist(), vec.tolist())
        results.append((sim, text))
    results.sort(reverse=True)

    print(f"\n   {'Rank':<5} {'Score':>7}  Text")
    print(f"   {'-'*5} {'-'*7}  {'-'*40}")
    for i, (sim, text) in enumerate(results, 1):
        bar = "#" * int(sim * 30)
        print(f"   {i:<5} {sim:>7.3f}  {text:<40} {bar}")

    # 4. Cross-lingual test
    print("\n4. Cross-lingual similarity")
    pairs = [
        ("你好世界", "Hello World"),
        ("机器学习", "Machine Learning"),
        ("天气不错", "The weather is nice"),
        ("一只猫坐在垫子上", "A cat sits on the mat"),
    ]
    all_texts = [t for pair in pairs for t in pair]
    all_vecs = model.encode(all_texts, normalize_embeddings=True)
    for i, (cn, en) in enumerate(pairs):
        sim = cosine_similarity(all_vecs[i * 2], all_vecs[i * 2 + 1])
        print(f"   '{cn}' <-> '{en}'  sim={sim:.3f}")

    # 5. Performance test
    print("\n5. Performance test")
    batch = ["test message " + str(i) for i in range(100)]
    start = time.monotonic()
    _ = model.encode(batch, normalize_embeddings=True)
    elapsed = time.monotonic() - start
    print(f"   100 msgs in {elapsed:.2f}s ({elapsed/100*1000:.1f}ms/msg)")

    print("\n" + "=" * 55)
    print("All tests passed")
    print("=" * 55)
