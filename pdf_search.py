"""On-demand lookup for the local vocabulary book PDF.

The module never imports the book into SQLite.  It opens the PDF only when a
word is searched, locates the matching entry, and returns structured data for
the caller to decide whether to save.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import threading

import fitz


_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'-]*$")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_BRACKET_RE = re.compile(r"[\[［].*?[\]］]")


class PdfSearchError(RuntimeError):
    """Raised when the vocabulary book cannot be queried safely."""


@dataclass(frozen=True)
class PdfEntry:
    word: str
    definition: str
    phrases: tuple[str, ...]
    page_number: int


class PdfSearcher:
    """Search a single local PDF without building a persistent index."""

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        self._lock = threading.Lock()

    def search(self, word: str) -> PdfEntry | None:
        query = word.strip().lower()
        if not _WORD_RE.fullmatch(query):
            raise ValueError("请输入一个有效的英文单词。")
        if not self.pdf_path.is_file():
            raise PdfSearchError(f"找不到 PDF 词书：{self.pdf_path.name}")

        # PyMuPDF documents should not be shared across concurrent requests.
        with self._lock:
            try:
                with fitz.open(self.pdf_path) as document:
                    for page_index, page in enumerate(document):
                        # Fast rejection before asking for positioned words.
                        page_text = page.get_text("text").lower()
                        if query not in page_text:
                            continue
                        entry = self._extract_entry(page, query, page_index + 1)
                        if entry is not None:
                            return entry
            except (fitz.FileDataError, OSError) as exc:
                raise PdfSearchError("PDF 词书无法读取或文件已损坏。") from exc
        return None

    def _extract_entry(
        self, page: fitz.Page, query: str, page_number: int
    ) -> PdfEntry | None:
        words = page.get_text("words", sort=True)
        if not words:
            return None

        headings = self._find_headings(words)
        target_index = next(
            (i for i, heading in enumerate(headings) if heading["word"] == query),
            None,
        )
        if target_index is None:
            return None

        heading = headings[target_index]
        next_y = (
            headings[target_index + 1]["top"]
            if target_index + 1 < len(headings)
            else page.rect.height - 20
        )
        entry_words = [
            item for item in words if heading["top"] - 2 <= item[1] < next_y - 2
        ]

        definition = self._extract_definition(entry_words, query, heading["top"])
        phrases = self._extract_phrases(
            entry_words,
            query,
            heading["bottom"] + 4,
            page.rect.width,
        )
        if not definition:
            return None
        return PdfEntry(query, definition, tuple(phrases), page_number)

    @staticmethod
    def _find_headings(words: list[tuple]) -> list[dict[str, float | str]]:
        """Find entry headings by the word followed by a phonetic bracket."""
        headings: list[dict[str, float | str]] = []
        for index, item in enumerate(words):
            text = item[4].strip().lower()
            if not _WORD_RE.fullmatch(text):
                continue
            nearby = words[index + 1 : index + 8]
            has_phonetic = any(
                candidate[0] > item[0]
                and abs(candidate[1] - item[1]) <= 9
                and ("[" in candidate[4] or "［" in candidate[4])
                for candidate in nearby
            )
            if has_phonetic:
                headings.append(
                    {
                        "word": text,
                        "top": item[1],
                        "bottom": item[3],
                    }
                )
        return headings

    @staticmethod
    def _extract_definition(words: list[tuple], query: str, top: float) -> str:
        heading_words = [item for item in words if abs(item[1] - top) <= 9]
        heading_text = " ".join(item[4] for item in sorted(heading_words, key=lambda x: x[0]))
        heading_text = re.sub(rf"^.*?\b{re.escape(query)}\b", "", heading_text, count=1, flags=re.I)
        heading_text = _BRACKET_RE.sub("", heading_text, count=1)
        return " ".join(heading_text.split()).strip(" -")

    @staticmethod
    def _extract_phrases(
        words: list[tuple], query: str, body_top: float, page_width: float
    ) -> list[str]:
        rows: list[list[tuple]] = []
        for item in sorted((w for w in words if w[1] >= body_top), key=lambda x: (x[1], x[0])):
            if not rows or abs(rows[-1][0][1] - item[1]) > 8:
                rows.append([item])
            else:
                rows[-1].append(item)

        stem = query[:-1] if query.endswith("e") and len(query) > 5 else query
        results: list[str] = []
        for row in rows:
            for segment in (
                [item for item in row if item[0] < page_width / 2],
                [item for item in row if item[0] >= page_width / 2],
            ):
                if not segment:
                    continue
                text = " ".join(item[4] for item in sorted(segment, key=lambda x: x[0]))
                compact = re.sub(r"\s+", "", text.lower())
                if stem not in compact or not _CJK_RE.search(text):
                    continue
                if any(marker in text for marker in ("［派］", "[派]", "［近］", "[近]", "词根", "后缀")):
                    continue
                first_letter = re.search(r"[A-Za-z]", text)
                first_chinese = _CJK_RE.search(text)
                if not first_letter or not first_chinese or first_letter.start() >= first_chinese.start():
                    continue
                phrase = " ".join(text[first_letter.start() :].split()).strip()
                spaced_query = re.compile(
                    r"\s*".join(re.escape(character) for character in query), re.I
                )
                phrase = spaced_query.sub(query, phrase)
                phrase = re.sub(r"^(?:Gil|Q)\s+", "", phrase, flags=re.I)
                phrase = re.sub(r"\s+(?:OO|00)$", "", phrase)
                if phrase and phrase not in results:
                    results.append(phrase)
        return results[:8]


def find_vocabulary_pdf(directory: str | Path) -> Path:
    """Return the only PDF book in a directory, rejecting ambiguous setup."""
    pdf_files = sorted(Path(directory).glob("*.pdf"))
    if not pdf_files:
        raise PdfSearchError("项目目录中没有找到 PDF 词书。")
    if len(pdf_files) > 1:
        raise PdfSearchError("项目目录中有多个 PDF，请在配置中明确指定词书。")
    return pdf_files[0]
