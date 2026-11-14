"""向量检索通道：faiss SQ8（int8）内存粗排 + fp16 mmap 磁盘精排。

内存方案（用户确认的 int8 量化方案）：
- faiss ScalarQuantizer QT_8bit 索引驻内存（92.8 万条 × 512 维 ≈ 475MB），
  负责粗排 top candidate_n；
- fp16 原始向量存磁盘（npy），mmap 取候选行做精排重打分；
- 编码模型（bge-small-zh-v1.5）惰性加载：首次真正检索时才 import
  sentence-transformers 并加载权重，纯 CPU、无显存占用。
"""

from __future__ import annotations

import json
import threading

from .corpus import QA_META

VECTOR_SQ8 = "vector_sq8.faiss"
VECTOR_FP16 = "emb_fp16.npy"


class VectorIndexError(RuntimeError):
    """向量通道不可用（缺产物 / 模型加载失败）。"""


class VectorIndex:
    def __init__(self, kb_dir, model_dir: str, dims: int, n: int,
                 model_name: str = "") -> None:
        import faiss
        import numpy as np

        from pathlib import Path

        root = Path(kb_dir)
        index_path = root / VECTOR_SQ8
        fp16_path = root / VECTOR_FP16
        if not index_path.is_file() or not fp16_path.is_file():
            raise VectorIndexError(
                f"向量产物缺失：{VECTOR_SQ8} / {VECTOR_FP16}（先跑 scripts/build_kb.py）"
            )
        self.faiss_index = faiss.read_index(str(index_path))
        self.emb_fp16 = np.load(fp16_path, mmap_mode="r")
        self.dims = int(dims)
        self.n = int(n)
        self.model_dir = str(model_dir)
        self.model_name = str(model_name)
        self._encoder = None
        self._encoder_error: str | None = None
        self._lock = threading.Lock()

    @classmethod
    def try_load(cls, kb_dir, model_dir: str) -> tuple["VectorIndex | None", str]:
        """从 kb_meta.json 读维度并加载；失败返回 (None, 原因) 供降级。"""
        from pathlib import Path

        meta_path = Path(kb_dir) / QA_META
        if not meta_path.is_file():
            return None, f"缺少 {QA_META}（先跑 scripts/build_kb.py）"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"读取 {QA_META} 失败：{exc}"
        try:
            index = cls(
                kb_dir=kb_dir,
                model_dir=model_dir,
                dims=int(meta.get("dims", 0)),
                n=int(meta.get("rows", 0)),
                model_name=str(meta.get("model_name", "")),
            )
        except VectorIndexError as exc:
            return None, str(exc)
        except Exception as exc:  # noqa: BLE001 - faiss/numpy 缺失等
            return None, f"向量索引加载失败：{type(exc).__name__}: {exc}"
        return index, ""

    @property
    def encoder_ready(self) -> bool:
        return self._encoder is not None

    # ---------- 编码 ----------
    def encode(self, query: str):
        """query → 归一化 fp32 向量 (dims,)。模型惰性加载，双检锁。"""
        import numpy as np

        if self._encoder is None:
            with self._lock:
                if self._encoder is None:
                    if self._encoder_error:
                        raise VectorIndexError(self._encoder_error)
                    if not self.model_dir:
                        raise VectorIndexError("未配置 embedding 模型目录")
                    try:
                        from sentence_transformers import SentenceTransformer

                        model = SentenceTransformer(
                            self.model_dir, device="cpu",
                            model_kwargs={"torch_dtype": "float32"},
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._encoder_error = (
                            f"embedding 模型加载失败（{self.model_dir}）："
                            f"{type(exc).__name__}: {exc}"
                        )
                        raise VectorIndexError(self._encoder_error) from exc
                    self._encoder = model
        vec = self._encoder.encode(
            [str(query)], normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False,
        )[0]
        return np.asarray(vec, dtype=np.float32)

    # ---------- 检索 ----------
    def search(self, query: str, top_n: int = 50) -> list[tuple[int, float]]:
        """粗排（SQ8）→ fp16 精排，返回 [(row, score)] 降序。"""
        import numpy as np

        q = self.encode(query)
        top_n = max(1, min(int(top_n), self.n))
        coarse = min(top_n * 2, self.n)
        _, rows = self.faiss_index.search(q[None, :], coarse)
        rows = rows[0]
        rows = rows[rows >= 0]
        if rows.size == 0:
            return []
        cand = np.asarray(self.emb_fp16[rows], dtype=np.float32)
        scores = cand @ q
        order = np.argsort(-scores)[:top_n]
        return [(int(rows[i]), float(scores[i])) for i in order]
