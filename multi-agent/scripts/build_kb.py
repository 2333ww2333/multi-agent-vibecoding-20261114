"""离线构建农业问答知识库（parquet → BM25 倒排 + 向量 int8 索引）—— CLI 壳。

用法（在项目根目录、激活 .venv 后）：

    .venv\\Scripts\\python.exe scripts\\build_kb.py ^
        --parquet "D:\\path\\to\\Chinese-QA-AFAF-train-v2.parquet" ^
        --model "D:\\path\\to\\models\\bge-small-zh-v1.5" ^
        --out ../kb

产物（运行时按行惰性读取，response 不进内存）：
    qa_ids.bin / qa_ids_off.npy           id 文本块 + 偏移
    qa_prompts.bin / qa_prompts_off.npy   prompt 文本块 + 偏移
    qa_responses.bin / qa_responses_off.npy
    bm25_terms.json + bm25_*               numpy 倒排
    emb_fp16.npy                           fp16 向量（磁盘 mmap 精排）
    vector_sq8.faiss                       faiss int8 索引（内存粗排）
    kb_meta.json                           构建信息

--skip-vector 可只建 BM25（模型未就绪时先跑检索通道）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.agri.builder import build_all  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="构建农业问答知识库索引")
    parser.add_argument("--parquet", required=True, help="parquet 语料路径")
    parser.add_argument("--model", default="", help="bge-small-zh-v1.5 模型目录")
    parser.add_argument("--out", default="data/kb", help="产物目录（默认 data/kb）")
    parser.add_argument("--batch-size", type=int, default=256, help="编码批大小")
    parser.add_argument("--limit", type=int, default=None, help="只构建前 N 行（调试）")
    parser.add_argument("--skip-vector", action="store_true", help="跳过向量构建")
    args = parser.parse_args()

    parquet_path = Path(args.parquet).expanduser()
    if not parquet_path.is_file():
        raise SystemExit(f"parquet 不存在：{parquet_path}")
    out_dir = Path(args.out).expanduser()
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    if not args.skip_vector and not args.model:
        raise SystemExit("需要 --model 指定 embedding 模型目录（或 --skip-vector）")

    meta = build_all(
        parquet_path, out_dir,
        model_dir=args.model,
        limit=args.limit,
        skip_vector=args.skip_vector,
        batch_size=args.batch_size,
    )
    print(f"构建完成：{out_dir}")
    print(
        f"rows={meta['rows']:,} terms={meta['bm25']['terms']:,} "
        f"dims={meta['dims']}"
    )
    print(
        "下一步：.env 设置 AGRI_CORPUS_PATH / AGRI_KB_DIR 指向该产物目录"
        "（任意非 .json 路径即切换 qa 模式），"
        "AGRI_EMBEDDING_MODEL_DIR 指向 bge-small-zh-v1.5 模型目录，AGRI_KB_ENABLED=1"
    )


if __name__ == "__main__":
    main()
