from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from backend.app.config import MEMORY_DIR, PROJECT_ROOT
from backend.app.schemas import UploadedFileSummary


ANALYSIS_TIMEOUT_SECONDS = int(os.getenv("OPSAGENT_TRANSCRIPTOMICS_ANALYSIS_TIMEOUT_SECONDS", "300"))
PLOTLY_BUNDLE = Path(__file__).resolve().parents[1] / "vendor" / "plotly-3.5.1.min.js"
R_SCRIPT = Path(__file__).resolve().parents[1] / "r" / "differential_transcriptomics.R"
DEFAULT_PADJ_CUTOFF = 0.05
DEFAULT_LOG2_FC_CUTOFF = 1.0


@dataclass(frozen=True)
class RRunResult:
    completed: subprocess.CompletedProcess[str]
    command: list[str]
    stdout_path: Path
    stderr_path: Path


def run_differential_transcriptomics_analysis(
    attachments: list[UploadedFileSummary],
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intake = _select_ready_intake(attachments)
    if "error" in intake:
        return intake

    comparisons = _choose_comparisons(intake["groups"], arguments or {})
    if not comparisons:
        return {
            "error": "无法确定转录组差异分析比较组，请在问题中明确两个分组，或上传可自动配对的分组矩阵。",
            "detected_groups": intake["groups"],
            "filename": intake["filename"],
        }

    parameters = _analysis_parameters(arguments or {})
    run_id = uuid.uuid4().hex
    output_dir = MEMORY_DIR / "artifacts" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    comparisons_path = output_dir / "comparisons.csv"
    pd.DataFrame(comparisons).to_csv(comparisons_path, index=False)

    rscript = _find_rscript()
    if rscript is None:
        return {
            "error": "未找到 Rscript。请安装 R，或设置 OPSAGENT_RSCRIPT_PATH 指向 Rscript.exe。",
            "source_file": intake["source_file"],
        }

    command = _r_command(
        rscript,
        Path(str(intake["matrix_file"])),
        Path(str(intake["sample_metadata_file"])),
        comparisons_path,
        output_dir,
        parameters,
    )
    first_run = _run_r_command(command, output_dir)
    if first_run.completed.returncode != 0:
        retry = _retry_failed_transcriptomics_run(
            first_run,
            intake,
            comparisons_path,
            output_dir,
            rscript,
            parameters,
        )
        if retry.get("status") == "retry_succeeded":
            retry_intake = {**intake, "matrix_file": retry["matrix_file"]}
            summaries = _summarize_results(output_dir, comparisons, parameters)
            report_path = _write_report(output_dir, retry_intake, summaries, parameters)
            return {
                "status": "completed",
                "analysis": "differential_transcriptomics_analysis",
                "source_file": intake["source_file"],
                "run_id": run_id,
                "comparison_count": len(summaries),
                "comparisons": summaries,
                "groups": intake["groups"],
                "feature_count": retry["feature_count"],
                "sample_count": intake["sample_count"],
                "parameters": parameters,
                "retry": retry["retry"],
                "files": {
                    "report_html": str(report_path),
                    "report_url": f"/api/artifacts/{run_id}/report.html",
                    "normalized_counts": str(output_dir / "normalized_counts.csv"),
                    "comparisons": str(comparisons_path),
                    "repaired_standard_matrix": retry["matrix_file"],
                    "retry_plan": str(output_dir / "retry_plan.json"),
                },
            }
        return _r_failure_result(first_run, intake, retry.get("retry"))

    summaries = _summarize_results(output_dir, comparisons, parameters)
    report_path = _write_report(output_dir, intake, summaries, parameters)
    return {
        "status": "completed",
        "analysis": "differential_transcriptomics_analysis",
        "source_file": intake["source_file"],
        "run_id": run_id,
        "comparison_count": len(summaries),
        "comparisons": summaries,
        "groups": intake["groups"],
        "feature_count": intake["feature_count"],
        "sample_count": intake["sample_count"],
        "parameters": parameters,
        "files": {
            "report_html": str(report_path),
            "report_url": f"/api/artifacts/{run_id}/report.html",
            "normalized_counts": str(output_dir / "normalized_counts.csv"),
            "comparisons": str(comparisons_path),
        },
    }


def _r_command(
    rscript: Path,
    matrix_file: Path,
    metadata_file: Path,
    comparisons_path: Path,
    output_dir: Path,
    parameters: dict[str, float],
) -> list[str]:
    return [
        str(rscript),
        str(R_SCRIPT),
        str(matrix_file),
        str(metadata_file),
        str(comparisons_path),
        str(output_dir),
        str(parameters["padj_cutoff"]),
        str(parameters["log2_fc_cutoff"]),
    ]


def _run_r_command(command: list[str], output_dir: Path, suffix: str = "") -> RRunResult:
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=ANALYSIS_TIMEOUT_SECONDS,
        check=False,
    )
    stdout_path = output_dir / f"r_stdout{suffix}.txt"
    stderr_path = output_dir / f"r_stderr{suffix}.txt"
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    return RRunResult(completed=completed, command=command, stdout_path=stdout_path, stderr_path=stderr_path)


def _r_failure_result(run: RRunResult, intake: dict[str, Any], retry: dict[str, Any] | None = None) -> dict[str, Any]:
    stderr = (run.completed.stderr or "").strip()
    error = "R 转录组差异分析执行失败。"
    if _is_missing_deseq2_error(stderr):
        error = "R 环境缺少 DESeq2，无法执行转录组差异分析。"
    return {
        "error": error,
        "returncode": run.completed.returncode,
        "stderr": stderr[-4000:],
        "stdout": (run.completed.stdout or "").strip()[-2000:],
        "source_file": intake["source_file"],
        "retry": retry or {"attempted": False, "reason": "没有匹配到可安全自动修复的失败类型。"},
    }


def _retry_failed_transcriptomics_run(
    first_run: RRunResult,
    intake: dict[str, Any],
    comparisons_path: Path,
    output_dir: Path,
    rscript: Path,
    parameters: dict[str, float],
) -> dict[str, Any]:
    first_stderr = (first_run.completed.stderr or "").strip()
    if _is_missing_deseq2_error(first_stderr):
        return {"status": "not_retryable", "retry": {"attempted": False, "reason": "缺少 DESeq2 属于环境错误，不能通过重跑修复。"}}

    repair = _sanitize_counts_matrix_for_deseq2(
        Path(str(intake["matrix_file"])),
        Path(str(intake["sample_metadata_file"])),
        output_dir / "retry_standard_matrix.csv",
    )
    retry_plan = {
        "attempted": bool(repair["retryable"]),
        "action": "sanitize_counts_matrix",
        "reason": "第一次 DESeq2 执行失败，调用层只按白名单规则清理 counts 矩阵后重跑固定 R 脚本。",
        "first_returncode": first_run.completed.returncode,
        "first_stderr_tail": first_stderr[-2000:],
        "repair": repair,
    }
    (output_dir / "retry_plan.json").write_text(json.dumps(retry_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if not repair["retryable"]:
        return {"status": "not_retryable", "retry": retry_plan}

    command = _r_command(
        rscript,
        Path(str(repair["matrix_file"])),
        Path(str(intake["sample_metadata_file"])),
        comparisons_path,
        output_dir,
        parameters,
    )
    retry_run = _run_r_command(command, output_dir, suffix="_retry")
    retry_plan["second_returncode"] = retry_run.completed.returncode
    retry_plan["second_stderr_tail"] = (retry_run.completed.stderr or "").strip()[-2000:]
    (output_dir / "retry_plan.json").write_text(json.dumps(retry_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if retry_run.completed.returncode != 0:
        return {"status": "retry_failed", "retry": retry_plan}
    return {
        "status": "retry_succeeded",
        "matrix_file": str(repair["matrix_file"]),
        "feature_count": int(repair["remaining_rows"]),
        "retry": retry_plan,
    }


def _is_missing_deseq2_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return "deseq2 package is required" in lowered or (
        "there is no package called" in lowered and "deseq2" in lowered
    )


def _sanitize_counts_matrix_for_deseq2(matrix_file: Path, metadata_file: Path, output_path: Path) -> dict[str, Any]:
    matrix = pd.read_csv(matrix_file)
    metadata = pd.read_csv(metadata_file)
    samples = [str(sample) for sample in metadata.get("sample", []) if str(sample) in matrix.columns]
    if not samples:
        return {
            "retryable": False,
            "reason": "标准矩阵中找不到 sample_metadata 对应的样本列。",
            "matrix_file": str(output_path),
            "removed_rows": 0,
            "remaining_rows": 0,
            "invalid_cells": 0,
        }

    repaired = matrix.copy()
    numeric = repaired[samples].apply(lambda column: pd.to_numeric(column.astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce"))
    invalid_cells = int(numeric.isna().sum().sum())
    negative_cells = int((numeric < 0).sum().sum())
    numeric = numeric.round()
    invalid_rows = numeric.isna().any(axis=1) | (numeric < 0).any(axis=1) | (numeric.sum(axis=1) <= 0)
    cleaned = repaired.loc[~invalid_rows].copy()
    for sample in samples:
        cleaned[sample] = numeric.loc[~invalid_rows, sample].astype(int).to_list()
    removed_rows = int(invalid_rows.sum())
    if removed_rows == 0:
        return {
            "retryable": False,
            "reason": "未发现可通过清理 counts 行修复的数据问题。",
            "matrix_file": str(output_path),
            "removed_rows": 0,
            "remaining_rows": int(len(cleaned)),
            "invalid_cells": invalid_cells,
            "negative_cells": negative_cells,
        }
    if cleaned.empty:
        return {
            "retryable": False,
            "reason": "清理非法 counts 后没有剩余基因，不能重跑。",
            "matrix_file": str(output_path),
            "removed_rows": removed_rows,
            "remaining_rows": 0,
            "invalid_cells": invalid_cells,
            "negative_cells": negative_cells,
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(output_path, index=False)
    return {
        "retryable": True,
        "reason": "已删除含非数值、缺失、负数或全零 counts 的基因行，并将 counts 四舍五入为整数。",
        "matrix_file": str(output_path),
        "removed_rows": removed_rows,
        "remaining_rows": int(len(cleaned)),
        "invalid_cells": invalid_cells,
        "negative_cells": negative_cells,
    }


def _select_ready_intake(attachments: list[UploadedFileSummary]) -> dict[str, Any]:
    selected: UploadedFileSummary | None = None
    for item in attachments:
        intake = item.intake or {}
        if (
            intake.get("status") == "ready"
            and intake.get("data_family") == "transcriptomics"
            and intake.get("data_type") == "expression_matrix"
        ):
            selected = item
            break
    if selected is None:
        return {
            "error": "当前会话没有已完成 intake 的转录组 counts 表达矩阵，请先上传 RNA-seq counts CSV/TSV/TXT/XLSX 文件。",
        }

    intake = selected.intake or {}
    standard_files = intake.get("standard_files") or {}
    matrix_file = Path(str(standard_files.get("matrix") or ""))
    metadata_file = Path(str(standard_files.get("sample_metadata") or ""))
    if not matrix_file.is_file() or not metadata_file.is_file():
        return {
            "error": "上传文件的标准化结果不存在，请重新上传后再分析。",
            "file_id": selected.file_id,
            "filename": selected.filename,
        }

    groups = {
        str(group): [str(sample) for sample in samples]
        for group, samples in (intake.get("sample_groups") or {}).items()
        if isinstance(samples, list)
    }
    if len(groups) < 2:
        return {
            "error": "intake 没有识别到至少两个转录组样本分组。",
            "file_id": selected.file_id,
            "filename": selected.filename,
            "detected_groups": groups,
        }
    return {
        "filename": selected.filename,
        "source_file": selected.path or "",
        "matrix_file": str(matrix_file),
        "sample_metadata_file": str(metadata_file),
        "feature_count": int(intake.get("feature_count") or 0),
        "sample_count": int(intake.get("sample_count") or 0),
        "groups": groups,
    }


def _choose_comparisons(groups: dict[str, list[str]], arguments: dict[str, Any]) -> list[dict[str, str]]:
    requested = []
    for item in arguments.get("comparisons") or []:
        if not isinstance(item, dict):
            continue
        numerator = str(item.get("numerator") or "")
        denominator = str(item.get("denominator") or "")
        if numerator in groups and denominator in groups and numerator != denominator:
            comparison = _comparison(numerator, denominator)
            if comparison not in requested:
                requested.append(comparison)
    if requested:
        return requested
    if len(groups) == 2:
        names = list(groups)
        return [_comparison(names[0], names[1])]
    return _paired_comparisons(groups)


def _paired_comparisons(groups: dict[str, list[str]]) -> list[dict[str, str]]:
    suffix_groups: dict[str, dict[str, str]] = {}
    for group in groups:
        split = re.match(r"^([A-Za-z0-9]+)[_\-. ](.+)$", group)
        if not split:
            continue
        prefix, suffix = split.group(1), split.group(2)
        suffix_groups.setdefault(suffix.lower(), {})[prefix.upper()] = group

    comparisons: list[dict[str, str]] = []
    for suffix in sorted(suffix_groups):
        paired = suffix_groups[suffix]
        if "MT" in paired and "WT" in paired:
            comparisons.append(_comparison(paired["MT"], paired["WT"]))
            continue
        if len(paired) == 2:
            numerator, denominator = list(paired.values())
            comparisons.append(_comparison(numerator, denominator))
    return comparisons


def _comparison(numerator: str, denominator: str) -> dict[str, str]:
    comparison = f"{numerator} vs {denominator}"
    return {
        "slug": _slug(f"{numerator}vs{denominator}"),
        "comparison": comparison,
        "numerator": numerator,
        "denominator": denominator,
    }


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.-")
    return cleaned or "comparison"


def _analysis_parameters(arguments: dict[str, Any]) -> dict[str, float]:
    return {
        "padj_cutoff": _argument_cutoff(arguments.get("padj_cutoff"), DEFAULT_PADJ_CUTOFF, lambda value: 0 < value < 1),
        "log2_fc_cutoff": _argument_cutoff(
            arguments.get("log2_fc_cutoff"),
            DEFAULT_LOG2_FC_CUTOFF,
            lambda value: value >= 0,
        ),
    }


def _argument_cutoff(value: Any, default: float, valid: Any) -> float:
    try:
        cutoff = float(value)
    except (TypeError, ValueError):
        return default
    return cutoff if valid(cutoff) else default


def _find_rscript() -> Path | None:
    env_path = os.getenv("OPSAGENT_RSCRIPT_PATH")
    candidates = [
        Path(env_path) if env_path else None,
        Path(found) if (found := shutil.which("Rscript")) else None,
        Path(r"C:\Program Files\R\R-4.6.0\bin\Rscript.exe"),
        Path(r"C:\Program Files\R\R-4.5.0\bin\Rscript.exe"),
        Path(r"C:\Program Files\R\R-4.4.0\bin\Rscript.exe"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def _summarize_results(
    output_dir: Path,
    comparisons: list[dict[str, str]],
    parameters: dict[str, float],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for comparison in comparisons:
        all_path = output_dir / f"{comparison['slug']}_all_genes.csv"
        results = pd.read_csv(all_path)
        regulation = results.get("regulation", pd.Series(dtype=str))
        summaries.append(
            {
                **comparison,
                "total": int(len(results)),
                "significant": int((regulation != "not_significant").sum()),
                "up": int((regulation == "up").sum()),
                "down": int((regulation == "down").sum()),
                "padj_cutoff": parameters["padj_cutoff"],
                "log2_fc_cutoff": parameters["log2_fc_cutoff"],
                "files": {
                    "all_genes": str(all_path),
                    "significant_genes": str(output_dir / f"{comparison['slug']}_significant_genes.csv"),
                    "up_genes": str(output_dir / f"{comparison['slug']}_up_genes.csv"),
                    "down_genes": str(output_dir / f"{comparison['slug']}_down_genes.csv"),
                },
            }
        )
    return summaries


def _write_report(
    output_dir: Path,
    intake: dict[str, Any],
    summaries: list[dict[str, Any]],
    parameters: dict[str, float],
) -> Path:
    normalized = pd.read_csv(output_dir / "normalized_counts.csv")
    payload: dict[str, Any] = {}
    for summary in summaries:
        results = pd.read_csv(summary["files"]["all_genes"])
        payload[summary["slug"]] = {
            "summary": _public_summary(summary),
            "volcano": _volcano_points(results),
            "heatmap": _heatmap_payload(normalized, results, summary, intake["groups"]),
        }
    report_path = output_dir / "report.html"
    if PLOTLY_BUNDLE.is_file():
        shutil.copyfile(PLOTLY_BUNDLE, output_dir / "plotly.min.js")
    report_path.write_text(_report_html(payload, summaries, parameters), encoding="utf-8")
    return report_path


def _public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if key != "files"}


def _volcano_points(results: pd.DataFrame) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in results.head(8000).to_dict(orient="records"):
        padj = _safe_float(row.get("padj"))
        log2_fc = _safe_float(row.get("log2FoldChange"))
        if padj is None or log2_fc is None or padj <= 0:
            continue
        points.append(
            {
                "id": str(row.get("gene_id", "")),
                "x": log2_fc,
                "y": -math.log10(padj),
                "padj": padj,
                "pvalue": _safe_float(row.get("pvalue")),
                "regulation": str(row.get("regulation", "not_significant")),
            }
        )
    return points


def _heatmap_payload(
    normalized: pd.DataFrame,
    results: pd.DataFrame,
    summary: dict[str, Any],
    groups: dict[str, list[str]],
) -> dict[str, Any]:
    significant = results[results["regulation"] != "not_significant"].head(40)
    selected = significant if not significant.empty else results.head(40)
    ids = set(selected["gene_id"].astype(str))
    rows = normalized[normalized["gene_id"].astype(str).isin(ids)].head(40)
    samples = groups.get(summary["numerator"], []) + groups.get(summary["denominator"], [])
    payload_rows: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        raw_values = [_safe_float(row.get(sample)) for sample in samples]
        finite = [value for value in raw_values if value is not None]
        mean = sum(finite) / len(finite) if finite else 0
        variance = sum((value - mean) ** 2 for value in finite) / len(finite) if finite else 0
        sd = math.sqrt(variance) if variance > 0 else 1
        payload_rows.append(
            {
                "id": str(row.get("gene_id", "")),
                "values": [None if value is None else max(-3, min(3, (value - mean) / sd)) for value in raw_values],
            }
        )
    payload_rows = _cluster_heatmap_rows(payload_rows)
    return {"samples": samples, "rows": payload_rows, "row_order": "average_linkage"}


def _cluster_heatmap_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) < 3:
        return rows

    vectors = [_cluster_vector(row["values"]) for row in rows]
    clusters = [[index] for index in range(len(rows))]
    while len(clusters) > 1:
        best_pair = (0, 1)
        best_distance = float("inf")
        for left in range(len(clusters)):
            for right in range(left + 1, len(clusters)):
                distance = _average_cluster_distance(clusters[left], clusters[right], vectors)
                if distance < best_distance:
                    best_distance = distance
                    best_pair = (left, right)
        left, right = best_pair
        first = clusters[left]
        second = clusters[right]
        merged = _merge_cluster_order(first, second, vectors)
        clusters = [cluster for index, cluster in enumerate(clusters) if index not in best_pair]
        clusters.append(merged)
    return [rows[index] for index in clusters[0]]


def _cluster_vector(values: list[float | None]) -> list[float]:
    return [0.0 if value is None else float(value) for value in values]


def _average_cluster_distance(left: list[int], right: list[int], vectors: list[list[float]]) -> float:
    distances = [_vector_distance(vectors[left_index], vectors[right_index]) for left_index in left for right_index in right]
    return sum(distances) / max(len(distances), 1)


def _merge_cluster_order(left: list[int], right: list[int], vectors: list[list[float]]) -> list[int]:
    candidates = [
        left + right,
        left + list(reversed(right)),
        right + left,
        right + list(reversed(left)),
    ]
    return min(
        candidates,
        key=lambda order: sum(
            _vector_distance(vectors[order[index - 1]], vectors[order[index]])
            for index in range(1, len(order))
        ),
    )


def _vector_distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((left_value - right_value) ** 2 for left_value, right_value in zip(left, right)))


def _report_html(payload: dict[str, Any], summaries: list[dict[str, Any]], parameters: dict[str, float]) -> str:
    options = "".join(
        f'<option value="{html.escape(summary["slug"])}">{html.escape(summary["comparison"])}</option>'
        for summary in summaries
    )
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(summary['comparison'])}</td>"
        f"<td>{summary['total']}</td><td>{summary['significant']}</td>"
        f"<td>{summary['up']}</td><td>{summary['down']}</td>"
        f'<td><a href="{html.escape(Path(summary["files"]["all_genes"]).name)}">all</a> '
        f'<a href="{html.escape(Path(summary["files"]["significant_genes"]).name)}">significant</a></td>'
        "</tr>"
        for summary in summaries
    )
    payload_json = json.dumps(payload, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Differential Transcriptomics Report</title>
  <style>
    :root {{ --panel:#fffdf8; --ink:#1f1d19; --muted:#73685b; --line:#d9cdbb; }}
    body {{ margin:0; background:linear-gradient(135deg,#f4ead8,#eaf2e8); color:var(--ink); font-family:'Avenir Next','Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; }}
    button,select {{ font:inherit; }}
    main {{ max-width:1180px; margin:32px auto; padding:0 24px 48px; }}
    .hero,section {{ border:1px solid var(--line); border-radius:22px; background:rgba(255,253,248,.9); padding:24px; margin-top:18px; box-shadow:0 18px 50px rgba(55,41,20,.1); }}
    h1,h2 {{ margin-top:0; }}
    .muted {{ color:var(--muted); line-height:1.6; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
    .plot {{ height:500px; min-height:420px; background:#fffaf1; border-radius:16px; }}
    table {{ width:100%; border-collapse:collapse; background:var(--panel); }}
    th,td {{ border-bottom:1px solid var(--line); padding:9px 10px; text-align:left; }}
    select {{ border:1px solid var(--line); border-radius:999px; background:#fffaf1; padding:8px 12px; }}
    a {{ color:#165c53; font-weight:700; }}
    @media (max-width:900px) {{ .grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
<main>
  <div class="hero">
    <h1>Differential Transcriptomics Analysis</h1>
    <div class="muted">DESeq2 thresholds: adjusted p-value &lt; {parameters['padj_cutoff']}; |log2 fold change| &gt;= {parameters['log2_fc_cutoff']}.</div>
    <label>Comparison <select id="comparison">{options}</select></label>
  </div>
  <section>
    <h2>Comparison Summary</h2>
    <table><thead><tr><th>Comparison</th><th>Tested genes</th><th>Significant</th><th>Up</th><th>Down</th><th>Results</th></tr></thead><tbody>{rows}</tbody></table>
  </section>
  <div class="grid">
    <section><h2>Volcano Plot</h2><div id="volcano" class="plot"></div></section>
    <section><h2>Clustered Heatmap</h2><div id="heatmap" class="plot"></div></section>
  </div>
</main>
<script src="plotly.min.js"></script>
<script>
const payload = {payload_json};
const config = {{responsive:true, displaylogo:false, displayModeBar:false, scrollZoom:true}};
const paper = {{paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"#fffaf1", font:{{family:"Avenir Next, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif", color:"#1f1d19"}}}};
function draw(slug) {{
  const item = payload[slug];
  const volcano = item.volcano;
  const trace = (regulation, name, color) => {{
    const points = volcano.filter(point => point.regulation === regulation);
    return {{
      type:"scattergl", mode:"markers", name,
      x:points.map(point => point.x), y:points.map(point => point.y),
      text:points.map(point => point.id),
      customdata:points.map(point => [point.padj, point.pvalue]),
      marker:{{color, size:regulation === "not_significant" ? 5 : 8, opacity:regulation === "not_significant" ? .36 : .82}},
      hovertemplate:"<b>%{{text}}</b><br>log2FC=%{{x:.3f}}<br>-log10(padj)=%{{y:.3f}}<br>padj=%{{customdata[0]:.3e}}<extra>" + name + "</extra>"
    }};
  }};
  Plotly.react("volcano", [
    trace("up", "Up", "#c94132"),
    trace("down", "Down", "#2f6fab"),
    trace("not_significant", "Not significant", "#9a9084")
  ], {{
    ...paper, margin:{{l:92,r:18,t:12,b:78}}, hovermode:"closest",
    xaxis:{{title:{{text:"log2 fold change", standoff:16}}, automargin:true, gridcolor:"#eadfce"}},
    yaxis:{{title:{{text:"-log10 adjusted p-value", standoff:16}}, automargin:true, gridcolor:"#eadfce"}}
  }}, config);
  const heatmap = item.heatmap;
  Plotly.react("heatmap", [{{
    type:"heatmap", x:heatmap.samples, y:heatmap.rows.map(row => row.id),
    z:heatmap.rows.map(row => row.values), zmin:-3, zmax:3,
    colorscale:[[0,"#2f6fab"],[.5,"#f7efe2"],[1,"#c94132"]],
    hovertemplate:"<b>%{{y}}</b><br>sample=%{{x}}<br>z-score=%{{z:.3f}}<extra></extra>"
  }}], {{
    ...paper, margin:{{l:180,r:18,t:12,b:72}},
    xaxis:{{title:"Sample", automargin:true}},
    yaxis:{{title:"Gene axis", autorange:"reversed", automargin:true}}
  }}, config);
}}
const selector = document.getElementById("comparison");
selector.onchange = () => draw(selector.value);
draw(selector.value);
</script>
</body>
</html>"""


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
