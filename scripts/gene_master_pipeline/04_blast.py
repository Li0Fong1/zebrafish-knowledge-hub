# -*- coding: utf-8 -*-
"""步骤 4：BLAST 兜底 —— 序列级映射 GRCz11 → GRCz12tu CDS。

4a) 从 Ensembl GRCz11 cDNA 里按 ensembl_id / zfin_id / symbol 取最长转录本作 query
4b) blastn 打 GRCz12tu CDS 库，按 identity/coverage/evalue 判定落点

用法：python step4_blast.py [stage]   stage: extract | blast | all
"""
import io, os, sys, gzip, re, json, time, subprocess
from collections import defaultdict, Counter

RT = "/data/zoujin/deng/zebrafish_runtime"
GM = RT + "/gene_map"
BLAST = RT + "/conda_blast/bin"
DB = RT + "/blast/grcz12tu_cds_db"
CDNA = "/data/zoujin/deng/zebrafish_runtime/omics_structured/01_Ensembl_GRCz11/Danio_rerio.GRCz11.cdna.all.fa.gz"

RE_GENE = re.compile(r"\bgene:(ENSDARG\d+)")
RE_ZFIN = re.compile(r"Source:ZFIN;Acc:(ZDB-GENE-[\w-]+)")
RE_SYM = re.compile(r"\bgene_symbol:([^\s]+)")


def load_tsv(path):
    with io.open(path, encoding="utf-8") as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            v = line.rstrip("\n").split("\t")
            if len(v) < len(hdr):
                v += [""] * (len(hdr) - len(v))
            yield dict(zip(hdr, v))


def stage_extract():
    """解析 GRCz11 cDNA，建三种键的索引，给 pending 基因取序列。"""
    pending = list(load_tsv(GM + "/pending_blast.tsv"))
    print("待 BLAST %d 个" % len(pending))

    want_ens = {}
    want_zfin = {}
    want_sym = {}
    for r in pending:
        if r["ensembl_id"]:
            want_ens.setdefault(r["ensembl_id"], r["gene_id"])
        if r["zfin_id"]:
            want_zfin.setdefault(r["zfin_id"], r["gene_id"])
        if r["gene_name"]:
            want_sym.setdefault(r["gene_name"].lower(), r["gene_id"])
    print("线索：ensembl %d, zfin %d, symbol %d" % (len(want_ens), len(want_zfin), len(want_sym)))

    t0 = time.time()
    best = {}          # gene_id -> (len, seq, transcript_id)
    hit_by = Counter()
    cur = []
    with gzip.open(CDNA, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(">"):
                if cur:
                    _flush(cur, want_ens, want_zfin, want_sym, best, hit_by)
                cur = [line.rstrip("\n"), ""]
            elif cur:
                cur[1] += line.strip()
        if cur:
            _flush(cur, want_ens, want_zfin, want_sym, best, hit_by)
    print("cDNA 解析 %.1fs，取到序列 %d 个" % (time.time() - t0, len(best)))
    print("命中方式:", json.dumps(dict(hit_by), ensure_ascii=False))

    with io.open(GM + "/blast_query.fa", "w", encoding="utf-8", newline="\n") as fh:
        for gid, (ln, seq, tid) in best.items():
            fh.write(">%s\n" % gid)
            for i in range(0, len(seq), 70):
                fh.write(seq[i:i + 70] + "\n")
    print("query 写入 %s" % (GM + "/blast_query.fa"))

    missing = [r for r in pending if r["gene_id"] not in best]
    with io.open(GM + "/no_sequence.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("gene_id\tgene_name\tgene_symbol\tensembl_id\tzfin_id\tuniprot_id\tchromosome\n")
        for r in missing:
            fh.write("\t".join(str(r.get(k, "")) for k in
                     ("gene_id", "gene_name", "gene_symbol", "ensembl_id", "zfin_id", "uniprot_id", "chromosome")) + "\n")
    print("无序列可用 %d 个 -> no_sequence.tsv" % len(missing))


def _flush(cur, want_ens, want_zfin, want_sym, best, hit_by):
    head, seq = cur
    gid = None
    m = RE_GENE.search(head)
    if m and m.group(1).split(".")[0] in want_ens:
        gid = want_ens[m.group(1).split(".")[0]]; hit_by["ensembl"] += 1
    if gid is None:
        m = RE_ZFIN.search(head)
        if m and m.group(1) in want_zfin:
            gid = want_zfin[m.group(1)]; hit_by["zfin"] += 1
    if gid is None:
        m = RE_SYM.search(head)
        if m:
            k = m.group(1).lower()
            if k in want_sym:
                gid = want_sym[k]; hit_by["symbol"] += 1
    if gid is None or not seq:
        return
    prev = best.get(gid)
    if prev is None or len(seq) > prev[0]:
        best[gid] = (len(seq), seq, head[1:].split()[0])


def stage_blast():
    """跑 blastn 并解析落点。"""
    q = GM + "/blast_query.fa"
    out = GM + "/blast_hits.tsv"
    if not os.path.isfile(DB + ".nsq") and not os.path.isfile(DB + ".nhr"):
        print("BLAST 库尚未就绪：%s" % DB)
        return 1
    t0 = time.time()
    cmd = [BLAST + "/blastn", "-task", "blastn", "-query", q, "-db", DB,
           "-outfmt", "6 qseqid sseqid pident length evalue bitscore stitle",
           "-max_target_seqs", "8", "-num_threads", "32", "-evalue", "1e-5", "-out", out]
    print("运行:", " ".join(cmd[:6]), "...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("blastn 完成 %.1fs rc=%s" % (time.time() - t0, r.returncode))
    if r.stderr.strip():
        print("[stderr]", r.stderr[:600])

    # 从 stitle 直接解析 GeneID —— 比 sseqid 可靠：makeblastdb 对 lcl| 前缀的 ID
    # 会改写 sseqid（实测输出 NC_133177.1_cds_6758，与 FASTA 头对不上），
    # 但 stitle 保留原始描述，含 [db_xref=GeneID:...]。
    best = {}
    nline = 0
    no_gid = 0
    RE_GID = re.compile(r"GeneID:(\d+)")
    with io.open(out, encoding="utf-8") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 6:
                continue
            nline += 1
            qid, pid, ln, ev, bs = p[0], float(p[2]), int(p[3]), float(p[4]), float(p[5])
            title = p[6] if len(p) > 6 else ""
            m = RE_GID.search(title)
            if not m:
                no_gid += 1
                continue
            gid = m.group(1)
            prev = best.get(qid)
            # 以 bitscore 为主排序；同分时取 identity 高的
            if prev is None or bs > prev[2] or (bs == prev[2] and pid > prev[1]):
                best[qid] = (gid, pid, bs, ln, ev)

    print("hits 行 %d，缺 GeneID %d，有落点 query %d" % (nline, no_gid, len(best)))
    with io.open(GM + "/blast_map.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("gene_id\tgrcz12tu_gene_id\tpident\tbitscore\talign_len\tevalue\n")
        for qid, (gid, pid, bs, ln, ev) in best.items():
            fh.write("%s\t%s\t%.2f\t%.0f\t%d\t%s\n" % (qid, gid, pid, bs, ln, ev))
    print("BLAST 有命中 %d 个 -> blast_map.tsv" % len(best))
    for th in (95, 90, 80, 70):
        hi = sum(1 for v in best.values() if v[1] >= th)
        print("   identity>=%d%%: %d 个" % (th, hi))
    return 0


if __name__ == "__main__":
    st = sys.argv[1] if len(sys.argv) > 1 else "all"
    if st in ("all", "extract"):
        stage_extract()
    if st in ("all", "blast"):
        stage_blast()
