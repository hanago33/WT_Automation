# encoding: utf-8
"""项目知识库（轻量 RAG，零外部依赖）。

扫描项目内沉淀的知识文档（.qoder/repowiki、docs、Help_document、skills 等），
按 Markdown 标题切分为片段，建立关键词倒排索引，按需检索最相关片段注入模型上下文，
让 Agent 能基于自有知识库回答项目功能、底层逻辑、使用说明等问题。
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 知识源根目录（仅扫描存在的目录）
DEFAULT_ROOTS = [
    PROJECT_ROOT / ".qoder" / "repowiki",
    PROJECT_ROOT / "docs",
    PROJECT_ROOT / "WT_AUTOMATION_Agent" / "Help_document",
    PROJECT_ROOT / "skills",
    PROJECT_ROOT / "WT_AUTOMATION_Agent" / "skills",
]

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _tokenize(text: str) -> list[str]:
    """中文按字 + 二元组，英文/数字按词。"""
    toks: list[str] = []
    toks.extend(m.group(0).lower() for m in _WORD.finditer(text or ""))
    for m in _CJK.finditer(text or ""):
        s = m.group(0)
        toks.extend(s)
        for i in range(len(s) - 1):
            toks.append(s[i:i + 2])
    return toks


def _chunk_text(text: str, max_chars: int = 1200) -> list[str]:
    """按空行分段，过长再切。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) > max_chars:
            chunks.append(buf)
            buf = p
        else:
            buf = (buf + "\n\n" + p).strip() if buf else p
    if buf:
        chunks.append(buf)
    return chunks


class KnowledgeBase:
    def __init__(self) -> None:
        self.chunks: list[dict[str, Any]] = []
        self.index: dict[str, list[tuple[int, float]]] = {}  # term -> [(chunk_id, tf)]
        self.df: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self.built = False
        self._building = False

    # ---- 构建 ----
    def build(self, roots: list[Path] | None = None) -> None:
        if self.built or self._building:
            return
        self._building = True
        try:
            roots = roots or [r for r in DEFAULT_ROOTS if r.exists()]
            for root in roots:
                self._index_dir(root)
            n = max(len(self.chunks), 1)
            self._idf = {t: math.log(n / (c + 1)) + 1 for t, c in self.df.items()}
            self.built = True
        finally:
            self._building = False

    def _index_dir(self, root: Path) -> None:
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if fn.lower().endswith((".md", ".markdown", ".txt")):
                    self._index_file(Path(dirpath) / fn)

    def _index_file(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        rel = (str(path.relative_to(PROJECT_ROOT))
               if PROJECT_ROOT in path.parents else path.name)
        lines = text.splitlines()
        cur_heading: list[str] = []
        cur_body: list[str] = []

        def flush():
            if not cur_body and not cur_heading:
                return
            heading_title = " / ".join(cur_heading).strip() or rel
            body = "\n".join(cur_body).strip()
            if body:
                for piece in _chunk_text(body):
                    self._add_chunk(rel, heading_title, piece)

        for line in lines:
            m = _HEADING.match(line)
            if m:
                flush()
                level = len(m.group(1))
                htext = m.group(2).strip()
                cur_heading = cur_heading[:level - 1] + [htext]
                cur_body = []
            else:
                cur_body.append(line)
        flush()

    def _add_chunk(self, source: str, title: str, text: str) -> None:
        cid = len(self.chunks)
        tf: dict[str, float] = {}
        for t in _tokenize(text):
            tf[t] = tf.get(t, 0.0) + 1.0
        for t, c in tf.items():
            self.index.setdefault(t, []).append((cid, c))
            self.df[t] = self.df.get(t, 0) + 1
        self.chunks.append({
            "id": cid, "source": source, "title": title, "text": text,
        })

    # ---- 检索 ----
    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        self.build()
        if not self.chunks:
            return []
        q_toks = _tokenize(query)
        if not q_toks:
            return []
        scores: dict[int, float] = {}
        for t in set(q_toks):
            postings = self.index.get(t)
            if not postings:
                continue
            idf = self._idf.get(t, 1.0)
            for cid, tf in postings:
                scores[cid] = scores.get(cid, 0.0) + tf * idf
        # 路径/标题命中加权
        q_lower = (query or "").lower()
        if q_lower:
            for cid, sc in scores.items():
                if q_lower in self.chunks[cid]["source"].lower():
                    scores[cid] = sc + 2.0
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            {
                "source": self.chunks[cid]["source"],
                "title": self.chunks[cid]["title"],
                "score": round(sc, 3),
                "text": self.chunks[cid]["text"],
            }
            for cid, sc in ranked[:top_k]
        ]

    def build_context(self, query: str, top_k: int = 5, max_chars: int = 3500) -> str:
        hits = self.retrieve(query, top_k=top_k)
        if not hits:
            return ""
        parts: list[str] = []
        total = 0
        for h in hits:
            block = f"【{h['title']}】\n来源: {h['source']}\n{h['text']}"
            if total + len(block) > max_chars:
                remaining = max_chars - total
                if remaining > 200:
                    parts.append(block[:remaining] + "\n...(已截断)")
                break
            parts.append(block)
            total += len(block)
        return "\n\n".join(parts)

    def status(self) -> dict[str, Any]:
        # 按顶层区域聚合（repowiki 知识库 / docs / Agent 帮助 / 技能库）。
        # Windows 下 source 用反斜杠，需同时按 / 与 \ 切分。
        def _top(src: str) -> str:
            return re.split(r"[\\/]", src)[0]

        areas: dict[str, int] = {}
        for c in self.chunks:
            top = _top(str(c["source"]))
            area = {
                ".qoder": "repowiki 知识库",
                "docs": "项目文档 docs",
                "WT_AUTOMATION_Agent": "Agent 说明/技能",
                "skills": "技能库 skills",
            }.get(top, top)
            areas[area] = areas.get(area, 0) + 1
        return {
            "built": self.built,
            "sources": len({c["source"] for c in self.chunks}),
            "chunks": len(self.chunks),
            "areas": dict(sorted(areas.items(), key=lambda x: -x[1])),
        }

    def list_sources(self) -> list[dict[str, Any]]:
        seen: dict[str, int] = {}
        for c in self.chunks:
            seen[c["source"]] = seen.get(c["source"], 0) + 1
        return [{"source": s, "chunks": n}
                for s, n in sorted(seen.items())]


_kb: KnowledgeBase | None = None


def get_knowledge_base() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    _kb.build()
    return _kb


def rebuild() -> KnowledgeBase:
    global _kb
    _kb = KnowledgeBase()
    _kb.build()
    return _kb
