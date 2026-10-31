# -*- coding: utf-8 -*-
"""增量向量化脚本：从 MySQL 读 gap 文档 → Qwen3 embedding → 写入 FAISS shard → 更新 manifest + MySQL

用法（在服务器上）：
  cd /data/zoujin/deng/zebrafish_gene_qa
  export ZEBRA_BASE=/data/zoujin/deng/zebrafish_gene_qa ZEBRA_RUNTIME=/data/zoujin/deng/zebrafish_runtime
  CUDA_VISIBLE_DEVICES=1 /data/zoujin/deng/miniconda3/envs/zebrafish/bin/python -m scripts.gap_vectorize_qwen3
"""
from __future__ import annotations
import os, sys, json, time, pickle, pathlib
from datetime import datetime
from pathlib import Path

os.environ.setdefault("FAISS_DISABLE_AVX2", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

BASE = Path(os.environ.get("ZEBRA_BASE", "/data/zoujin/deng/zebrafish_gene_qa"))
RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
FAISS_DIR = RUNTIME / "faiss_pubmed_yes_qwen3_4b_v1"
MODEL_DIR = RUNTIME / "models" / "Qwen3-Embedding-4B"
MANIFEST = FAISS_DIR / "manifest.json"
LOG = RUNTIME / "logs" / "gap_vectorize.log"
EMBED_DIM = 2048
SHARD_PAPERS = 20

def _log(msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def load_done_doc_ids() -> set:
    """从 FAISS shard 目录中读取所有已向量化的 doc_id"""
    done = set()
    si = {}
    if MANIFEST.exists():
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
        si = m.get("shard_info", {})

    for shard_id_str, info in si.items():
        # manifest 的 shard_info 只记录 first_doc/last_doc
        for key in ("first_doc", "last_doc", "first_pmid", "last_pmid"):
            v = info.get(key)
            if v: done.add(str(v))

    # 更可靠：直接从 shard 目录的 index.pkl 读取（langchain FAISS 格式）
    import pickle as pkl
    shard_dirs = sorted(FAISS_DIR.glob("shard_*"))
    _log(f"scanning {len(shard_dirs)} shard dirs for index.pkl …")
    pkl_count = 0
    for sd in shard_dirs:
        idx_pkl = sd / "index.pkl"
        if not idx_pkl.exists():
            continue
        try:
            with idx_pkl.open("rb") as f:
                obj = pkl.load(f)
            # langchain FAISS: index.pkl = tuple(InMemoryDocstore, index_to_docstore_id)
            if isinstance(obj, tuple) and len(obj) >= 1:
                store = obj[0]
            elif hasattr(obj, "_dict"):
                store = obj
            elif isinstance(obj, dict):
                store = obj
            else:
                continue
            if not hasattr(store, "_dict"):
                continue
            pkl_count += 1
            for doc in store._dict.values():
                meta = doc.metadata if hasattr(doc, "metadata") else {}
                eno = str(meta.get("entry_no") or "").strip()
                pmid = str(meta.get("pmid") or "").strip()
                if eno: done.add(f"eno:{eno}")
                if pmid: done.add(f"pmid:{pmid}")
        except Exception:
            pass
    _log(f"scanned {pkl_count} index.pkl files; done_doc_ids total = {len(done)}")
    return done

def get_mysql_gold_rows():
    """从 MySQL 获取所有 gold 记录的 doc_id + pmid + entry_no + sections_json"""
    import pymysql
    from dotenv import load_dotenv
    load_dotenv(str(BASE / ".env"))
    conn = pymysql.connect(
        host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"], charset="utf8mb4")
    cur = conn.cursor(pymysql.cursors.DictCursor)
    cur.execute("SELECT g.doc_id, g.sections_json, p.pmid, p.entry_no, p.title_en "
                "FROM literature_fulltext_gold g "
                "JOIN literature_paper p ON g.doc_id = p.doc_id "
                "WHERE g.sections_json IS NOT NULL AND CHAR_LENGTH(g.sections_json)>10")
    rows = cur.fetchall()
    conn.close()
    return rows

def load_rec_from_sections(doc_id: str, sections_json: str) -> dict | None:
    """从 sections_json 列解析论文记录"""
    if not sections_json:
        return None
    try:
        rec = json.loads(sections_json)
        rec["doc_id"] = doc_id
        return rec
    except Exception as e:
        _log(f"  WARN sections_json parse failed {doc_id}: {e}")
        return None

def chunk_rec(rec: dict) -> list[dict]:
    """将论文记录切块——顺序处理 sections → body_text → abstract，不互相排斥"""
    chunks = []
    title = str(rec.get("title") or rec.get("title_en") or "").strip()
    abstract = str(rec.get("abstract") or "").strip()
    body = str(rec.get("body") or rec.get("body_text") or "").strip()
    sections = rec.get("sections") or {}
    entry_no = str(rec.get("entry_no") or "")
    pmid = str(rec.get("pmid") or "")
    doc_id = str(rec.get("doc_id") or "")
    CS = 1500  # chunk size

    # 1. 按 section 切块（每个 section 独立切）
    if isinstance(sections, dict) and sections:
        for field in ("title", "abstract", "keywords", "introduction", "methods",
                       "results", "discussion", "conclusion", "other",
                       "figure_captions", "references"):
            text = str(sections.get(field) or "").strip()
            if not text or len(text) < 50:
                continue
            for i in range(0, len(text), CS):
                ct = text[i:i+CS]
                if len(ct.strip()) < 50:
                    continue
                chunks.append({
                    "text": ct, "entry_no": entry_no, "pmid": pmid,
                    "doc_id": doc_id, "field": field, "section": field,
                })

    # 2. sections 没产出 chunk → 用 body_text
    if not chunks and body and len(body) > 50:
        for i in range(0, len(body), CS):
            ct = body[i:i+CS]
            if len(ct.strip()) < 50:
                continue
            chunks.append({
                "text": ct, "entry_no": entry_no, "pmid": pmid,
                "doc_id": doc_id, "field": "body", "section": "body",
            })

    # 3. 还没 chunk → 用 abstract
    if not chunks and abstract:
        for i in range(0, len(abstract), CS):
            ct = abstract[i:i+CS]
            if len(ct.strip()) < 50:
                continue
            chunks.append({
                "text": ct, "entry_no": entry_no, "pmid": pmid,
                "doc_id": doc_id, "field": "abstract", "section": "abstract",
            })

    # 4. 最后 fallback: title
    if not chunks and title:
        chunks.append({
            "text": title, "entry_no": entry_no, "pmid": pmid,
            "doc_id": doc_id, "field": "title", "section": "title",
        })

    return chunks

def main():
    import torch

    _log("=== gap vectorize START ===")

    # 环境变量：支持双 GPU 并行
    INPUT_LIST = os.environ.get("INPUT_LIST", "")       # 从 JSONL 读论文列表
    SHARD_OFFSET = os.environ.get("SHARD_OFFSET", "")   # shard 起始 ID
    MANIFEST_FILE = os.environ.get("MANIFEST_FILE", "")  # 独立 manifest 文件
    BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "32"))  # tokenizer batch

    # 使用的 manifest
    manifest_path = Path(MANIFEST_FILE) if MANIFEST_FILE else MANIFEST

    if INPUT_LIST:
        # 模式 A：从 INPUT_LIST JSONL 读论文列表（双 GPU 并行模式）
        _log(f"INPUT_LIST mode: reading from {INPUT_LIST}")
        items = [json.loads(l) for l in open(INPUT_LIST, encoding="utf-8") if l.strip()]
        _log(f"loaded {len(items)} papers from INPUT_LIST")
        gap_rows = []
        for item in items:
            eno = str(item.get("entry_no") or "")
            doi = str(item.get("doi") or "")
            pmid = str(item.get("pmid") or "")
            doc_id = item.get("doc_id") or (f"eno:{eno}" if eno else f"doi:{doi}")
            # 从 body_path 文件读取完整 JSON
            bp = item.get("body_path") or item.get("file_path") or ""
            rec = None
            if bp and os.path.exists(bp):
                try:
                    rec = json.loads(open(bp, encoding="utf-8", errors="replace").read())
                    rec["doc_id"] = doc_id
                    if eno and not rec.get("entry_no"):
                        rec["entry_no"] = eno
                    if pmid and not rec.get("pmid"):
                        rec["pmid"] = pmid
                except Exception as e:
                    _log(f"  WARN body_path read failed {doc_id}: {e}")
            elif item.get("sections_json"):
                rec = load_rec_from_sections(doc_id, item["sections_json"])
            if rec:
                gap_rows.append((item, rec))
        _log(f"gap papers with valid rec = {len(gap_rows)}")
    else:
        # 模式 B：从 MySQL 读 + FAISS gap 检测（原始模式）
        done_ids = load_done_doc_ids()
        rows = get_mysql_gold_rows()
        _log(f"MySQL gold rows with sections_json = {len(rows)}")
        gap_rows = []
        for r in rows:
            doc_id = r["doc_id"]
            pmid = str(r.get("pmid") or "").strip()
            eno = str(r.get("entry_no") or "").strip()
            if doc_id in done_ids:
                continue
            if pmid and f"pmid:{pmid}" in done_ids:
                continue
            if eno and f"eno:{eno}" in done_ids:
                continue
            rec = load_rec_from_sections(doc_id, r["sections_json"])
            if rec:
                if eno and not rec.get("entry_no"):
                    rec["entry_no"] = eno
                if pmid and not rec.get("pmid"):
                    rec["pmid"] = pmid
                gap_rows.append((r, rec))
            else:
                gap_rows.append((r, None))
    _log(f"gap papers to vectorize = {len(gap_rows)}")

    if not gap_rows:
        _log("=== gap vectorize DONE: no gap ===")
        return 0

    # 4. 加载 Qwen3 模型（fp16 省显存）
    _log(f"loading Qwen3 model from {MODEL_DIR} (fp16) …")
    from transformers import AutoTokenizer, AutoModel
    import torch.nn.functional as F

    tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR), trust_remote_code=True)
    model = AutoModel.from_pretrained(str(MODEL_DIR), trust_remote_code=True,
                                      torch_dtype=torch.float16).cuda().eval()
    _log(f"model loaded on GPU (fp16); dim={EMBED_DIM}")

    def embed_texts(texts: list[str]) -> list[list[float]]:
        """嵌入文本列表（分批处理，控制 GPU 显存）"""
        if not texts:
            return []
        all_emb = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch_texts = texts[i:i+BATCH_SIZE]
            inputs = tokenizer(batch_texts, padding=True, truncation=True, max_length=512, return_tensors="pt").to("cuda")
            with torch.no_grad():
                outputs = model(**inputs)
            emb = outputs.last_hidden_state.mean(dim=1)
            emb = F.normalize(emb, p=2, dim=1)
            all_emb.extend(emb.cpu().tolist())
        return all_emb

    # 5. 读取 manifest（支持独立 manifest 文件）
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    si = manifest.get("shard_info", {})
    if SHARD_OFFSET:
        next_shard = int(SHARD_OFFSET)
    else:
        next_shard = max((int(k) for k in si if k.isdigit()), default=-1) + 1

    # 6. 切块 + embedding + 写 shard
    from langchain_core.documents import Document
    from langchain_community.vectorstores import FAISS

    # 创建一个临时 embedding 对象给 FAISS
    class QwenEmbed:
        def embed_documents(self, texts):
            return embed_texts(texts)
        def embed_query(self, text):
            return embed_texts([text])[0]

    emb_obj = QwenEmbed()

    batch = []
    batch_recs = []
    total_new_papers = 0
    total_new_chunks = 0

    for idx, (row, rec) in enumerate(gap_rows):
        if rec is None:
            _log(f"  SKIP {row['doc_id']}: no rec from body_path")
            continue

        chunks = chunk_rec(rec)
        if not chunks:
            _log(f"  SKIP {row['doc_id']}: no chunks")
            continue

        batch.append(rec)
        batch_recs.extend(chunks)

        if len(batch) >= SHARD_PAPERS or idx == len(gap_rows) - 1:
            _log(f"  shard_{next_shard:04d}: {len(batch)} papers → {len(batch_recs)} chunks …")

            docs = []
            for ch in batch_recs:
                docs.append(Document(
                    page_content=ch["text"],
                    metadata={
                        "pmid": ch.get("pmid") or "",
                        "entry_no": ch.get("entry_no") or "",
                        "doc_id": ch.get("doc_id") or "",
                        "field": ch.get("field") or "",
                        "section": ch.get("section") or ch.get("field") or "",
                        "source": "gap_vectorize",
                        "index_version": "qwen3_4b_v1",
                    },
                ))

            try:
                vs = FAISS.from_documents(docs, emb_obj)
                sd = FAISS_DIR / f"shard_{next_shard:04d}"
                sd.mkdir(parents=True, exist_ok=True)
                vs.save_local(str(sd))

                # 更新 manifest
                si[str(next_shard)] = {
                    "paper_count": len(batch),
                    "chunk_count": len(docs),
                    "kind": "gap_vectorize_qwen3_4b",
                    "first_doc": str(batch[0].get("doc_id") or ""),
                    "last_doc": str(batch[-1].get("doc_id") or ""),
                }
                manifest["shard_info"] = si
                manifest["shards"] = sorted(int(k) for k in si if k.isdigit())
                manifest["total_papers"] = int(manifest.get("total_papers") or 0) + len(batch)
                manifest["total_chunks"] = int(manifest.get("total_chunks") or 0) + len(docs)
                manifest["embedding_model"] = str(MODEL_DIR)
                manifest["embedding_dim"] = EMBED_DIM
                manifest["index_version"] = "qwen3_4b_v1"
                manifest["status"] = "building"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

                # 更新 MySQL vector_shard
                try:
                    import pymysql
                    from dotenv import load_dotenv
                    load_dotenv(str(BASE / ".env"))
                    conn = pymysql.connect(
                        host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
                        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                        database=os.environ["MYSQL_DATABASE"], charset="utf8mb4")
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO literature_vector_shard "
                        "(shard_id, index_version, root_path, shard_path, embedding_model, embedding_dim, paper_count, chunk_count, status, kind, manifest_json, created_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW()) "
                        "ON DUPLICATE KEY UPDATE paper_count=VALUES(paper_count), chunk_count=VALUES(chunk_count), updated_at=NOW()",
                        (int(next_shard), "qwen3_4b_v1", str(FAISS_DIR), str(sd), str(MODEL_DIR), EMBED_DIM, len(batch), len(docs), "ready", "gap_vectorize_qwen3_4b",
                         json.dumps({"first_doc": batch[0].get("doc_id"), "last_doc": batch[-1].get("doc_id")})))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    _log(f"  WARN MySQL vector_shard update failed: {e}")

                total_new_papers += len(batch)
                total_new_chunks += len(docs)
                _log(f"  wrote shard_{next_shard:04d} cumulative new_papers={total_new_papers} new_chunks={total_new_chunks}")
            except Exception as e:
                _log(f"  ERROR shard_{next_shard:04d} failed: {e}")

            torch.cuda.empty_cache()
            batch = []
            batch_recs = []
            next_shard += 1

    # 7. 最终更新 manifest
    manifest["status"] = "ready"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"=== gap vectorize DONE: {total_new_papers} new papers, {total_new_chunks} new chunks ===")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
