from __future__ import annotations

import csv
import html
import re
from pathlib import Path
from typing import Any

from backend.app.config import DATA_DIR


RESEARCH_PATH_DATASET = DATA_DIR / "final_atha_top30_integrated_output.csv"
MAX_MATCHES = 6
SECTION_LABELS = ("Hypothesis", "Methods", "Results", "Step_Conclusion")


def run_gene_function_research_path_query(message: str) -> dict[str, Any]:
    if not RESEARCH_PATH_DATASET.is_file():
        return {
            "error": f"基因功能研究路径数据文件不存在: {RESEARCH_PATH_DATASET}",
            "dataset": str(RESEARCH_PATH_DATASET),
        }

    rows = _read_rows(RESEARCH_PATH_DATASET)
    matched_rows = [row for row in rows if _mentions_gene(message, row.get("targetGene", ""))]
    if not matched_rows:
        return {
            "error": "没有在研究路径数据中匹配到基因。请提供拟南芥基因 symbol，例如 HY2、PHYA 或 ABI1。",
            "dataset": str(RESEARCH_PATH_DATASET),
            "available_gene_preview": sorted({row.get("targetGene", "") for row in rows if row.get("targetGene")})[:20],
        }

    matches = [_row_to_match(row, index) for index, row in enumerate(matched_rows[:MAX_MATCHES], start=1)]
    genes = sorted({match["gene_id"] for match in matches})
    return {
        "status": "completed",
        "analysis": "gene_function_research_path",
        "query": message,
        "genes": genes,
        "count": len(matches),
        "matches": matches,
        "ui_blocks": [_match_to_ui_block(match) for match in matches],
        "dataset": str(RESEARCH_PATH_DATASET),
    }


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _mentions_gene(message: str, gene: str) -> bool:
    cleaned = gene.strip()
    if not cleaned:
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(cleaned)}(?![A-Za-z0-9_.-])", message, re.I))


def _row_to_match(row: dict[str, str], index: int) -> dict[str, Any]:
    steps = parse_research_path_steps(row.get("final_md_content", ""))
    gene_id = row.get("targetGene", "").strip()
    return {
        "id": f"research-path-{_slug(gene_id)}-{_slug(row.get('paper_id', 'paper'))}-{index}",
        "paper_id": row.get("paper_id", "").strip(),
        "title": row.get("title", "").strip(),
        "gene_id": gene_id,
        "steps": steps,
    }


def parse_research_path_steps(markdown: str) -> list[dict[str, Any]]:
    text = str(markdown or "").replace("\r\n", "\n")
    route_heading = re.search(r"^##\s+Route of Gene Function Exploration\s*$", text, re.M)
    conclusion_heading = re.search(r"^##\s+Conclusion\s*$", text, re.M)
    route_end = conclusion_heading.start() if conclusion_heading else len(text)
    route_text = text[route_heading.end() : route_end] if route_heading else ""
    return _parse_route_table(route_text)


def _parse_route_table(route_text: str) -> list[dict[str, Any]]:
    rows: list[list[str]] = []
    for line in route_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 4 or cells[0].lower() == "steps" or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(cells[:4])

    steps = []
    for index, cells in enumerate(rows, start=1):
        details = _parse_step_detail(cells[3])
        step_value = cells[0].strip() or str(index)
        steps.append(
            {
                "step": step_value,
                "stage_operation": _clean_markdown_text(cells[1]),
                "figures": _clean_markdown_text(cells[2]),
                **details,
            }
        )
    return steps


def _parse_step_detail(markdown: str) -> dict[str, str]:
    text = html.unescape(str(markdown or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    label_pattern = re.compile(
        r"(?:^|\n)\s*-\s*(Hypothesis|Methods|Results|Step_Conclusion)\s*:\s*",
        re.I,
    )
    matches = list(label_pattern.finditer(text))
    values = {label.lower(): "" for label in SECTION_LABELS}
    for current, following in zip(matches, [*matches[1:], None]):
        label = current.group(1).lower()
        end = following.start() if following else len(text)
        values[label] = _clean_markdown_text(text[current.end() : end])
    return {
        "hypothesis": values["hypothesis"],
        "methods": values["methods"],
        "results": values["results"],
        "step_conclusion": values["step_conclusion"],
    }


def _clean_markdown_text(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _match_to_ui_block(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": match["id"],
        "type": "gene_function_research_path",
        "paper_id": match["paper_id"],
        "title": match["title"],
        "gene_id": match["gene_id"],
        "steps": match["steps"],
    }


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-").lower() or "item"
