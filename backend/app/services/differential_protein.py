from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from backend.app.config import MEMORY_DIR, PROJECT_ROOT
from backend.app.schemas import UploadedFileSummary


class DifferentialProteinError(ValueError):
    pass


ANALYSIS_TIMEOUT_SECONDS = int(os.getenv("OPSAGENT_ANALYSIS_TIMEOUT_SECONDS", "120"))


def run_differential_protein_analysis(
    message: str,
    attachments: list[UploadedFileSummary],
) -> dict[str, Any]:
    intake = _select_ready_intake(message, attachments)
    if "error" in intake:
        return intake

    run_id = uuid.uuid4().hex
    output_dir = MEMORY_DIR / "artifacts" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    rscript = _find_rscript()
    if rscript is None:
        return {
            "error": "未找到 Rscript。请安装 R，或设置 OPSAGENT_RSCRIPT_PATH 指向 Rscript.exe。",
            "source_file": intake["source_file"],
        }

    script_path = output_dir / "differential_analysis.R"
    script_path.write_text(_r_script(), encoding="utf-8")
    stdout_path = output_dir / "r_stdout.txt"
    stderr_path = output_dir / "r_stderr.txt"

    command = [
        str(rscript),
        str(script_path),
        str(intake["matrix_file"]),
        str(intake["sample_metadata_file"]),
        str(output_dir),
        intake["group_a"],
        intake["group_b"],
    ]
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=ANALYSIS_TIMEOUT_SECONDS,
        check=False,
    )
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        return {
            "error": "R 差异分析执行失败。",
            "returncode": completed.returncode,
            "stderr": (completed.stderr or "").strip()[-4000:],
            "stdout": (completed.stdout or "").strip()[-2000:],
            "source_file": intake["source_file"],
        }

    summary = _summarize_results(output_dir)
    report_path = _write_report(output_dir, intake, summary)
    return {
        "status": "completed",
        "analysis": "differential_protein_analysis",
        "source_file": intake["source_file"],
        "run_id": run_id,
        "comparison": f"{intake['group_b']} vs {intake['group_a']}",
        "groups": {
            intake["group_a"]: intake["group_a_samples"],
            intake["group_b"]: intake["group_b_samples"],
        },
        "feature_count": intake["feature_count"],
        "sample_count": intake["sample_count"],
        "summary": summary,
        "files": {
            "report_html": str(report_path),
            "report_url": f"/api/artifacts/{run_id}/report.html",
            "all_results": str(output_dir / "all_results.csv"),
            "differential_results": str(output_dir / "differential_results.csv"),
            "up_results": str(output_dir / "up_results.csv"),
            "down_results": str(output_dir / "down_results.csv"),
            "standard_matrix": str(intake["matrix_file"]),
            "sample_metadata": str(intake["sample_metadata_file"]),
        },
    }


def artifact_path(run_id: str, filename: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise DifferentialProteinError("Invalid artifact run id")
    root = (MEMORY_DIR / "artifacts" / run_id).resolve()
    target = (root / filename).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise DifferentialProteinError("Invalid artifact path") from exc
    if target == root:
        raise DifferentialProteinError("Invalid artifact path")
    return target


def _select_ready_intake(message: str, attachments: list[UploadedFileSummary]) -> dict[str, Any]:
    selected: UploadedFileSummary | None = None
    for item in attachments:
        intake = item.intake or {}
        if (
            intake.get("status") == "ready"
            and intake.get("data_family") == "proteomics"
            and intake.get("data_type") == "expression_matrix"
        ):
            selected = item
            break
    if selected is None:
        return {
            "error": "当前会话没有已完成 intake 的蛋白组定量矩阵，请先上传蛋白组 CSV/TSV/XLSX 文件。",
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

    groups = intake.get("sample_groups")
    if not isinstance(groups, dict):
        return {
            "error": "intake 没有识别到可用样本分组，请重新上传或补充分组信息。",
            "file_id": selected.file_id,
            "filename": selected.filename,
        }
    groups = {
        str(group): [str(sample) for sample in samples]
        for group, samples in groups.items()
        if isinstance(samples, list)
    }
    group_a, group_b = _choose_comparison(groups, message)
    if not group_a or not group_b:
        return {
            "error": "无法确定需要比较的两个分组，请在问题中明确写出分组名。",
            "detected_groups": groups,
            "file_id": selected.file_id,
            "filename": selected.filename,
        }
    if len(groups[group_a]) < 2 or len(groups[group_b]) < 2:
        return {
            "error": "每个分组至少需要 2 个样本才能进行 t 检验。",
            "detected_groups": groups,
            "selected_groups": [group_a, group_b],
            "filename": selected.filename,
        }
    return {
        "source_file": selected.path or "",
        "matrix_file": str(matrix_file),
        "sample_metadata_file": str(metadata_file),
        "feature_count": int(intake.get("feature_count") or 0),
        "sample_count": int(intake.get("sample_count") or 0),
        "group_a": group_a,
        "group_b": group_b,
        "group_a_samples": groups[group_a],
        "group_b_samples": groups[group_b],
    }


def _choose_comparison(groups: dict[str, list[str]], message: str) -> tuple[str | None, str | None]:
    if len(groups) == 2:
        names = list(groups)
        return names[0], names[1]

    mentioned = [group for group in groups if re.search(rf"\b{re.escape(group)}\b", message, re.I)]
    if len(mentioned) == 2:
        return mentioned[0], mentioned[1]
    return None


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


def _r_script() -> str:
    return r'''
args <- commandArgs(trailingOnly = TRUE)
matrix_file <- args[[1]]
metadata_file <- args[[2]]
output_dir <- args[[3]]
group_a <- args[[4]]
group_b <- args[[5]]

matrix <- read.csv(matrix_file, check.names = FALSE, stringsAsFactors = FALSE)
metadata <- read.csv(metadata_file, check.names = FALSE, stringsAsFactors = FALSE)
samples_a <- metadata$sample[metadata$condition == group_a]
samples_b <- metadata$sample[metadata$condition == group_b]

safe_num <- function(x) suppressWarnings(as.numeric(gsub(",", "", as.character(x))))

rows <- vector("list", nrow(matrix))
for (i in seq_len(nrow(matrix))) {
  values_a <- safe_num(unlist(matrix[i, samples_a, drop = FALSE]))
  values_b <- safe_num(unlist(matrix[i, samples_b, drop = FALSE]))
  values_a <- values_a[is.finite(values_a)]
  values_b <- values_b[is.finite(values_b)]
  mean_a <- if (length(values_a) > 0) mean(values_a) else NA
  mean_b <- if (length(values_b) > 0) mean(values_b) else NA
  fc <- if (is.finite(mean_a) && mean_a > 0 && is.finite(mean_b)) mean_b / mean_a else NA
  log2_fc <- if (is.finite(fc) && fc > 0) log2(fc) else NA
  pvalue <- NA
  if (length(values_a) >= 2 && length(values_b) >= 2) {
    pvalue <- tryCatch(t.test(values_b, values_a)$p.value, error = function(e) NA)
  }
  rows[[i]] <- data.frame(
    feature_id = matrix$feature_id[[i]],
    feature_name = matrix$feature_name[[i]],
    description = matrix$description[[i]],
    group_a = group_a,
    group_b = group_b,
    mean_a = mean_a,
    mean_b = mean_b,
    fold_change = fc,
    log2_fc = log2_fc,
    pvalue = pvalue,
    stringsAsFactors = FALSE
  )
}

result <- do.call(rbind, rows)
result$padj <- p.adjust(result$pvalue, method = "BH")
result$regulation <- "not_significant"
result$regulation[is.finite(result$fold_change) & is.finite(result$pvalue) & result$fold_change >= 1.5 & result$pvalue < 0.05] <- "up"
result$regulation[is.finite(result$fold_change) & is.finite(result$pvalue) & result$fold_change <= (2 / 3) & result$pvalue < 0.05] <- "down"
result <- result[order(result$pvalue, na.last = TRUE), ]

write.csv(result, file.path(output_dir, "all_results.csv"), row.names = FALSE, na = "")
write.csv(result[result$regulation != "not_significant", ], file.path(output_dir, "differential_results.csv"), row.names = FALSE, na = "")
write.csv(result[result$regulation == "up", ], file.path(output_dir, "up_results.csv"), row.names = FALSE, na = "")
write.csv(result[result$regulation == "down", ], file.path(output_dir, "down_results.csv"), row.names = FALSE, na = "")
'''


def _summarize_results(output_dir: Path) -> dict[str, Any]:
    all_results = pd.read_csv(output_dir / "all_results.csv")
    differential = all_results[all_results["regulation"] != "not_significant"]
    up = all_results[all_results["regulation"] == "up"]
    down = all_results[all_results["regulation"] == "down"]
    return {
        "total": int(len(all_results)),
        "differential": int(len(differential)),
        "up": int(len(up)),
        "down": int(len(down)),
        "pvalue_cutoff": 0.05,
        "fold_change_cutoff": 1.5,
    }


def _write_report(output_dir: Path, intake: dict[str, Any], summary: dict[str, Any]) -> Path:
    all_results = pd.read_csv(output_dir / "all_results.csv")
    matrix = pd.read_csv(intake["matrix_file"])
    volcano_points = _volcano_points(all_results)
    heatmap = _heatmap_payload(matrix, all_results, intake)
    rows = _table_rows(all_results.head(80))
    report_path = output_dir / "report.html"
    report_path.write_text(
        _report_html(intake, summary, volcano_points, heatmap, rows),
        encoding="utf-8",
    )
    return report_path


def _volcano_points(results: pd.DataFrame) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in results.head(5000).to_dict(orient="records"):
        pvalue = _safe_float(row.get("pvalue"))
        log2_fc = _safe_float(row.get("log2_fc"))
        if pvalue is None or log2_fc is None or pvalue <= 0:
            continue
        points.append(
            {
                "id": str(row.get("feature_id", "")),
                "name": str(row.get("feature_name", "")),
                "x": log2_fc,
                "y": -math.log10(pvalue),
                "regulation": str(row.get("regulation", "not_significant")),
                "pvalue": pvalue,
                "fold_change": _safe_float(row.get("fold_change")),
            }
        )
    return points


def _heatmap_payload(matrix: pd.DataFrame, results: pd.DataFrame, intake: dict[str, Any]) -> dict[str, Any]:
    selected = results[results["regulation"] != "not_significant"].head(40)
    if selected.empty:
        selected = results.head(40)
    ids = set(selected["feature_id"].astype(str))
    rows = matrix[matrix["feature_id"].astype(str).isin(ids)].head(40)
    samples = intake["group_a_samples"] + intake["group_b_samples"]
    payload_rows: list[dict[str, Any]] = []
    values: list[float] = []
    for _, row in rows.iterrows():
        raw_values = [_safe_float(row.get(sample)) for sample in samples]
        finite = [value for value in raw_values if value is not None]
        mean = sum(finite) / len(finite) if finite else 0
        variance = sum((value - mean) ** 2 for value in finite) / len(finite) if finite else 0
        sd = math.sqrt(variance) if variance > 0 else 1
        z_values = [None if value is None else max(-3, min(3, (value - mean) / sd)) for value in raw_values]
        values.extend([value for value in z_values if value is not None])
        payload_rows.append(
            {
                "id": str(row.get("feature_id", "")),
                "name": str(row.get("feature_name", "")),
                "values": z_values,
            }
        )
    return {"samples": samples, "rows": payload_rows, "min": -3, "max": 3}


def _table_rows(results: pd.DataFrame) -> str:
    cells: list[str] = []
    columns = ["feature_id", "feature_name", "fold_change", "log2_fc", "pvalue", "padj", "regulation"]
    for row in results[columns].to_dict(orient="records"):
        cells.append("<tr>" + "".join(f"<td>{html.escape(_format_cell(row.get(column)))}</td>" for column in columns) + "</tr>")
    return "\n".join(cells)


def _format_cell(value: Any) -> str:
    number = _safe_float(value)
    if number is not None:
        return f"{number:.4g}"
    return "" if value is None else str(value)


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _report_html(
    intake: dict[str, Any],
    summary: dict[str, Any],
    volcano_points: list[dict[str, Any]],
    heatmap: dict[str, Any],
    rows: str,
) -> str:
    volcano_json = json.dumps(volcano_points, ensure_ascii=False)
    heatmap_json = json.dumps(heatmap, ensure_ascii=False)
    comparison = html.escape(f"{intake['group_b']} vs {intake['group_a']}")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Differential Protein Report</title>
  <style>
    :root {{ --bg:#f6efe4; --panel:#fffdf8; --ink:#1f1d19; --muted:#73685b; --line:#d9cdbb; --up:#c94132; --down:#2f6fab; }}
    body {{ margin:0; background:linear-gradient(135deg,#f4ead8,#eaf2e8); color:var(--ink); font-family:Georgia, 'Times New Roman', serif; }}
    main {{ max-width:1180px; margin:32px auto; padding:0 24px 48px; }}
    .hero {{ border:1px solid var(--line); background:rgba(255,253,248,.86); border-radius:22px; padding:28px; box-shadow:0 18px 50px rgba(55,41,20,.12); }}
    h1 {{ margin:0 0 8px; font-size:34px; }}
    h2 {{ margin:30px 0 12px; font-size:24px; }}
    .muted {{ color:var(--muted); }}
    .cards {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-top:22px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:18px; }}
    .value {{ font-size:32px; font-weight:700; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
    section {{ background:rgba(255,253,248,.9); border:1px solid var(--line); border-radius:20px; padding:20px; margin-top:18px; overflow:auto; }}
    svg {{ width:100%; height:430px; background:#fffaf1; border-radius:14px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; background:#fffdf8; }}
    th,td {{ border-bottom:1px solid var(--line); padding:9px 10px; text-align:left; }}
    th {{ position:sticky; top:0; background:#f3eadb; }}
    .links a {{ display:inline-block; margin:8px 12px 0 0; color:#165c53; font-weight:700; }}
    .heatmap {{ display:grid; gap:2px; font-size:12px; min-width:720px; }}
    .hm-row {{ display:grid; grid-template-columns:180px repeat(var(--samples), 34px); gap:2px; align-items:center; }}
    .hm-cell {{ width:34px; height:20px; border-radius:3px; }}
    .hm-label {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    @media (max-width:900px) {{ .grid,.cards {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
<main>
  <div class="hero">
    <h1>Differential Protein Analysis</h1>
    <div class="muted">Comparison: {comparison}</div>
    <div class="cards">
      <div class="card"><div class="muted">Total proteins</div><div class="value">{summary['total']}</div></div>
      <div class="card"><div class="muted">Differential</div><div class="value">{summary['differential']}</div></div>
      <div class="card"><div class="muted">Up</div><div class="value">{summary['up']}</div></div>
      <div class="card"><div class="muted">Down</div><div class="value">{summary['down']}</div></div>
    </div>
    <div class="links">
      <a href="all_results.csv">all_results.csv</a>
      <a href="differential_results.csv">differential_results.csv</a>
      <a href="up_results.csv">up_results.csv</a>
      <a href="down_results.csv">down_results.csv</a>
    </div>
  </div>

  <div class="grid">
    <section>
      <h2>Volcano Plot</h2>
      <svg id="volcano" viewBox="0 0 760 430"></svg>
    </section>
    <section>
      <h2>Heatmap</h2>
      <div id="heatmap" class="heatmap"></div>
    </section>
  </div>

  <section>
    <h2>Top Results</h2>
    <table>
      <thead><tr><th>feature_id</th><th>feature_name</th><th>fold_change</th><th>log2_fc</th><th>pvalue</th><th>padj</th><th>regulation</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </section>
</main>
<script>
const volcano = {volcano_json};
const heatmap = {heatmap_json};
function drawVolcano() {{
  const svg = document.getElementById('volcano');
  const width = 760, height = 430, pad = 48;
  const xs = volcano.map(p => p.x), ys = volcano.map(p => p.y);
  const maxX = Math.max(1, ...xs.map(Math.abs));
  const maxY = Math.max(1, ...ys);
  const sx = x => pad + (x + maxX) / (2 * maxX) * (width - pad * 2);
  const sy = y => height - pad - y / maxY * (height - pad * 2);
  svg.innerHTML = `<line x1="${{pad}}" y1="${{height-pad}}" x2="${{width-pad}}" y2="${{height-pad}}" stroke="#b9aa95"/><line x1="${{pad}}" y1="${{pad}}" x2="${{pad}}" y2="${{height-pad}}" stroke="#b9aa95"/>`;
  for (const p of volcano) {{
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', sx(p.x)); c.setAttribute('cy', sy(p.y)); c.setAttribute('r', p.regulation === 'not_significant' ? 2.2 : 3.8);
    c.setAttribute('fill', p.regulation === 'up' ? '#c94132' : p.regulation === 'down' ? '#2f6fab' : '#9a9084');
    c.setAttribute('opacity', p.regulation === 'not_significant' ? '.45' : '.85');
    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    title.textContent = `${{p.name || p.id}} | log2FC=${{p.x.toFixed(3)}} | p=${{p.pvalue.toExponential(2)}}`;
    c.appendChild(title); svg.appendChild(c);
  }}
}}
function color(v) {{
  if (v === null || Number.isNaN(v)) return '#eee7dc';
  const t = Math.max(-3, Math.min(3, v));
  if (t >= 0) return `rgb(${{230}}, ${{Math.round(238 - t*42)}}, ${{Math.round(232 - t*54)}})`;
  return `rgb(${{Math.round(230 + t*42)}}, ${{Math.round(238 + t*24)}}, ${{245}})`;
}}
function drawHeatmap() {{
  const el = document.getElementById('heatmap');
  el.style.setProperty('--samples', heatmap.samples.length);
  const header = document.createElement('div');
  header.className = 'hm-row';
  header.innerHTML = '<strong>Protein</strong>' + heatmap.samples.map(s => `<strong title="${{s}}">${{s.slice(0,8)}}</strong>`).join('');
  el.appendChild(header);
  for (const row of heatmap.rows) {{
    const div = document.createElement('div');
    div.className = 'hm-row';
    div.innerHTML = `<div class="hm-label" title="${{row.name || row.id}}">${{row.name || row.id}}</div>` + row.values.map((v, i) => `<div class="hm-cell" title="${{heatmap.samples[i]}}: ${{v === null ? 'NA' : v.toFixed(2)}}" style="background:${{color(v)}}"></div>`).join('');
    el.appendChild(div);
  }}
}}
drawVolcano(); drawHeatmap();
</script>
</body>
</html>"""
