"""
Priority knowledge loader for local `knowledge` folder assets.

Primary behavior:
1) Load `instruction.md` as system prompt override.
2) Load `.md` and `.js` files as first-class reference context.
"""
from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("cafe_bot.priority_knowledge")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> set[str]:
    # Keep Korean / English / digits token candidates.
    tokens = re.findall(r"[0-9A-Za-z가-힣_./:-]+", text.lower())
    return {t for t in tokens if len(t) >= 2}


def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    current_title = "section"
    current_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_title, body))

    for line in lines:
        s = line.strip()
        if s.startswith("# ") or s.startswith("## ") or s.startswith("### "):
            flush()
            current_title = s.lstrip("#").strip() or "section"
            current_lines = [line]
            continue
        current_lines.append(line)
    flush()
    return sections


def _split_js_sections(text: str) -> list[tuple[str, str]]:
    """
    Split JS by top-level declarations and safe length boundaries.
    """
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    current_title = "code"
    current_lines: list[str] = []
    current_size = 0
    max_chars = 2200
    marker_re = re.compile(
        r"^\s*(?:function\s+([A-Za-z_][A-Za-z0-9_]*)|"
        r"class\s+([A-Za-z_][A-Za-z0-9_]*)|"
        r"(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=)"
    )

    def flush() -> None:
        nonlocal current_lines, current_size
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_title, body))
        current_lines = []
        current_size = 0

    for line in lines:
        marker = marker_re.match(line)
        if marker and current_lines:
            flush()
            name = marker.group(1) or marker.group(2) or marker.group(3) or "code"
            current_title = f"js::{name}"

        current_lines.append(line)
        current_size += len(line) + 1
        if current_size >= max_chars:
            flush()
            current_title = "js::chunk"

    flush()

    if not sections and text.strip():
        sections = [("js::all", text.strip())]
    return sections


@dataclass
class KnowledgeChunk:
    source_file: str
    section_title: str
    text: str
    normalized: str
    tokens: set[str]


class PriorityKnowledge:
    """
    Loads and serves context from a local folder path.
    Legacy `.zip` path is still accepted for backward compatibility.
    """

    def __init__(self, source_path: Path, instruction_file: str = "instruction.md"):
        self.source_path = Path(source_path)
        self.instruction_file = instruction_file
        self.instruction_text = ""
        self.chunks: list[KnowledgeChunk] = []
        self.loaded = False

    def _add_sections(self, source_file: str, sections: list[tuple[str, str]]) -> None:
        for section_title, section_text in sections:
            normalized = _normalize_text(section_text)
            if len(normalized) < 40:
                continue
            self.chunks.append(
                KnowledgeChunk(
                    source_file=source_file,
                    section_title=section_title,
                    text=section_text.strip(),
                    normalized=normalized.lower(),
                    tokens=_tokenize(section_text),
                )
            )

    def _load_from_directory(self, directory: Path) -> None:
        files = [
            p for p in sorted(directory.iterdir(), key=lambda x: x.name.lower())
            if p.is_file() and p.suffix.lower() in {".md", ".js"}
        ]

        for path in files:
            if path.name.lower() != self.instruction_file.lower():
                continue
            try:
                self.instruction_text = path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception as e:
                logger.warning("Failed to load instruction file (%s): %s", path, e)
            break

        for path in files:
            if path.name.lower() == self.instruction_file.lower():
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning("Failed to load priority file (%s): %s", path, e)
                continue

            if not raw.strip():
                continue

            if path.suffix.lower() == ".md":
                self._add_sections(path.name, _split_markdown_sections(raw))
            elif path.suffix.lower() == ".js":
                self._add_sections(path.name, _split_js_sections(raw))

    def _load_from_zip(self, zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            target_files = [
                n for n in names
                if Path(n).suffix.lower() in {".md", ".js"}
            ]

            for name in target_files:
                if Path(name).name.lower() != self.instruction_file.lower():
                    continue
                try:
                    self.instruction_text = zf.read(name).decode("utf-8", errors="ignore").strip()
                except Exception as e:
                    logger.warning("Failed to load instruction from zip (%s): %s", name, e)
                break

            for name in target_files:
                base = Path(name).name
                if base.lower() == self.instruction_file.lower():
                    continue
                try:
                    raw = zf.read(name).decode("utf-8", errors="ignore")
                except Exception as e:
                    logger.warning("Failed to load file from zip (%s): %s", name, e)
                    continue

                if not raw.strip():
                    continue

                suffix = Path(name).suffix.lower()
                if suffix == ".md":
                    self._add_sections(base, _split_markdown_sections(raw))
                elif suffix == ".js":
                    self._add_sections(base, _split_js_sections(raw))

    def load(self) -> None:
        self.loaded = False
        self.instruction_text = ""
        self.chunks = []

        if not self.source_path.exists():
            logger.warning("Priority knowledge source not found: %s", self.source_path)
            return

        try:
            if self.source_path.is_dir():
                self._load_from_directory(self.source_path)
            elif self.source_path.suffix.lower() == ".zip":
                self._load_from_zip(self.source_path)
            else:
                logger.warning("Unsupported priority source type: %s", self.source_path)
                return
        except Exception as e:
            logger.error("Failed to load priority knowledge: %s", e)
            return

        self.loaded = True
        logger.info(
            "Priority knowledge loaded: instruction=%s, chunks=%d",
            "yes" if bool(self.instruction_text) else "no",
            len(self.chunks),
        )

    def get_instruction_prompt(self) -> str:
        return self.instruction_text.strip()

    def retrieve_context(self, query: str, top_k: int = 4) -> str:
        if not self.loaded or not self.chunks:
            return ""

        q_norm = _normalize_text(query).lower()
        q_tokens = _tokenize(query)
        if not q_norm:
            return ""

        scored: list[tuple[float, KnowledgeChunk]] = []
        for chunk in self.chunks:
            overlap = len(q_tokens & chunk.tokens)
            if overlap == 0:
                # substring fallback for short/noisy queries
                if q_norm[:20] and q_norm[:20] in chunk.normalized:
                    overlap = 1
                else:
                    continue

            # lightweight score: overlap + phrase presence boost
            score = float(overlap)
            if q_norm in chunk.normalized:
                score += 2.0
            scored.append((score, chunk))

        if not scored:
            return ""

        scored.sort(key=lambda x: x[0], reverse=True)
        picked = scored[:max(1, top_k)]

        parts = []
        for i, (score, chunk) in enumerate(picked, 1):
            parts.append(
                f"--- 참고 {i} (출처: priority_local, 점수: {score:.1f}) ---\n"
                f"파일: {chunk.source_file}\n"
                f"섹션: {chunk.section_title}\n\n"
                f"{chunk.text}"
            )
        return "\n\n".join(parts)
