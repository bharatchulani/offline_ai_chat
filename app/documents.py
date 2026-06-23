from pathlib import Path

import fitz
from docx import Document


SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}


def extract_text(path: Path) -> str:
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {extension}")

    if extension in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if extension == ".pdf":
        with fitz.open(path) as document:
            return "\n".join(page.get_text() for page in document)
    if extension == ".docx":
        document = Document(path)
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    raise AssertionError("Unreachable")


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not cleaned:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + size, len(cleaned))
        if end < len(cleaned):
            boundary = max(cleaned.rfind("\n", start, end), cleaned.rfind(" ", start, end))
            if boundary > start + (size // 2):
                end = boundary
        chunks.append(cleaned[start:end].strip())
        if end == len(cleaned):
            break
        start = max(end - overlap, start + 1)
    return chunks
