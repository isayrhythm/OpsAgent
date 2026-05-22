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
PLOTLY_BUNDLE = Path(__file__).resolve().parents[1] / "vendor" / "plotly-3.5.1.min.js"
R_SCRIPT = Path(__file__).resolve().parents[1] / "r" / "differential_protein.R"
DEFAULT_PVALUE_CUTOFF = 0.05
DEFAULT_FOLD_CHANGE_CUTOFF = 1.5


def run_differential_protein_analysis(
    attachments: list[UploadedFileSummary],
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intake = _select_ready_intake(attachments, arguments or {})
    if "error" in intake:
        return intake

    parameters = _analysis_parameters(arguments or {})
    run_id = uuid.uuid4().hex
    output_dir = MEMORY_DIR / "artifacts" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    rscript = _find_rscript()
    if rscript is None:
        return {
            "error": "未找到 Rscript。请安装 R，或设置 OPSAGENT_RSCRIPT_PATH 指向 Rscript.exe。",
            "source_file": intake["source_file"],
        }

    stdout_path = output_dir / "r_stdout.txt"
    stderr_path = output_dir / "r_stderr.txt"

    command = [
        str(rscript),
        str(R_SCRIPT),
        str(intake["matrix_file"]),
        str(intake["sample_metadata_file"]),
        str(output_dir),
        intake["group_a"],
        intake["group_b"],
        str(parameters["pvalue_cutoff"]),
        str(parameters["fold_change_cutoff"]),
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

    summary = _summarize_results(output_dir, parameters)
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
        "parameters": parameters,
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


def _select_ready_intake(attachments: list[UploadedFileSummary], arguments: dict[str, Any]) -> dict[str, Any]:
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
    group_a, group_b = _choose_comparison(groups, arguments)
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


def _choose_comparison(groups: dict[str, list[str]], arguments: dict[str, Any]) -> tuple[str | None, str | None]:
    group_a = str(arguments.get("group_a") or "")
    group_b = str(arguments.get("group_b") or "")
    if group_a in groups and group_b in groups and group_a != group_b:
        return group_a, group_b
    if len(groups) == 2:
        names = list(groups)
        return names[0], names[1]
    return None


def _analysis_parameters(arguments: dict[str, Any]) -> dict[str, float]:
    return {
        "pvalue_cutoff": _argument_cutoff(arguments.get("pvalue_cutoff"), DEFAULT_PVALUE_CUTOFF, lambda value: 0 < value < 1),
        "fold_change_cutoff": _argument_cutoff(
            arguments.get("fold_change_cutoff"),
            DEFAULT_FOLD_CHANGE_CUTOFF,
            lambda value: value >= 1,
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


def _summarize_results(output_dir: Path, parameters: dict[str, float]) -> dict[str, Any]:
    all_results = pd.read_csv(output_dir / "all_results.csv")
    differential = all_results[all_results["regulation"] != "not_significant"]
    up = all_results[all_results["regulation"] == "up"]
    down = all_results[all_results["regulation"] == "down"]
    return {
        "total": int(len(all_results)),
        "differential": int(len(differential)),
        "up": int(len(up)),
        "down": int(len(down)),
        "pvalue_cutoff": parameters["pvalue_cutoff"],
        "fold_change_cutoff": parameters["fold_change_cutoff"],
    }


def _write_report(output_dir: Path, intake: dict[str, Any], summary: dict[str, Any]) -> Path:
    all_results = pd.read_csv(output_dir / "all_results.csv")
    matrix = pd.read_csv(intake["matrix_file"])
    volcano_points = _volcano_points(all_results)
    heatmap = _heatmap_payload(matrix, all_results, intake)
    rows = _table_rows(all_results.head(80))
    report_path = output_dir / "report.html"
    if PLOTLY_BUNDLE.is_file():
        shutil.copyfile(PLOTLY_BUNDLE, output_dir / "plotly.min.js")
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
    payload_rows = _cluster_heatmap_rows(payload_rows)
    return {"samples": samples, "rows": payload_rows, "min": -3, "max": 3, "row_order": "average_linkage"}


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
    :root {{ --bg:#f6efe4; --panel:#fffdf8; --ink:#1f1d19; --muted:#73685b; --line:#d9cdbb; --up:#c94132; --down:#2f6fab; --quiet:#8e877d; }}
    body {{ margin:0; background:linear-gradient(135deg,#f4ead8,#eaf2e8); color:var(--ink); font-family:'Avenir Next','Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; }}
    button,input {{ font:inherit; }}
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
    .plot {{ width:100%; height:480px; min-height:420px; border-radius:14px; background:#fffaf1; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; background:#fffdf8; }}
    th,td {{ border-bottom:1px solid var(--line); padding:9px 10px; text-align:left; }}
    th {{ position:sticky; top:0; background:#f3eadb; }}
    .links a {{ display:inline-block; margin:8px 12px 0 0; color:#165c53; font-weight:700; }}
    .chart-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:12px; }}
    .chart-head h2 {{ margin:0; }}
    .chart-tools {{ display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:8px; color:var(--muted); font-size:13px; }}
    .chart-tools button,.chart-tools input {{ border:1px solid var(--line); border-radius:999px; background:#fffaf1; color:var(--ink); }}
    .chart-tools button {{ padding:6px 11px; cursor:pointer; }}
    .chart-tools button.active {{ border-color:var(--accent,#165c53); background:#dcebe3; color:#15443b; }}
    .chart-tools input[type="search"] {{ width:150px; padding:7px 12px; }}
    .selection {{ min-height:22px; margin:10px 2px 0; color:var(--muted); font-size:13px; line-height:1.55; }}
    .selection strong {{ color:var(--ink); }}
    @media (max-width:900px) {{ .grid,.cards {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
<main>
  <div class="hero">
    <h1>Differential Protein Analysis</h1>
    <div class="muted">Comparison: {comparison}</div>
    <div class="muted">Thresholds: p-value &lt; {summary['pvalue_cutoff']}; fold change &gt;= {summary['fold_change_cutoff']} or &lt;= {1 / summary['fold_change_cutoff']:.4g}</div>
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
      <div class="chart-head">
        <h2>Volcano Plot</h2>
        <div class="chart-tools">
          <button id="volcanoAll" class="active" type="button">全部点</button>
          <button id="volcanoDiff" type="button">只看差异</button>
          <button id="volcanoReset" type="button">重置视图</button>
        </div>
      </div>
      <div id="volcano" class="plot"></div>
      <div id="volcanoSelection" class="selection">Plotly 火山图支持 hover、框选、缩放和平移；点击节点固定详情。</div>
    </section>
    <section>
      <div class="chart-head">
        <h2>Heatmap</h2>
        <div class="chart-tools">
          <input id="heatmapSearch" type="search" placeholder="筛选蛋白" />
        </div>
      </div>
      <div id="heatmap" class="plot"></div>
      <div id="heatmapSelection" class="selection">蛋白轴已按 z-score 表达模式做 average-linkage 聚类排序；Plotly 支持 hover、缩放和平移。</div>
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
<script src="plotly.min.js"></script>
<script>
const volcano = {volcano_json};
const heatmap = {heatmap_json};
const plotConfig = {{ responsive:true, displaylogo:false, displayModeBar:false, scrollZoom:true }};
const plotFont = {{ family:"Avenir Next, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif", color:"#1f1d19" }};
const plotPaper = {{ paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"#fffaf1", font:plotFont }};
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char]));
let volcanoDiffOnly = false;
function volcanoTrace(regulation, color, label, points) {{
  return {{
    type:"scattergl",
    mode:"markers",
    name:label,
    x:points.map(point => point.x),
    y:points.map(point => point.y),
    text:points.map(point => point.name || point.id),
    customdata:points.map(point => [point.id, point.fold_change, point.pvalue, point.regulation]),
    marker:{{ color, size:regulation === "not_significant" ? 5 : 8, opacity:regulation === "not_significant" ? .38 : .82 }},
    hovertemplate:"<b>%{{text}}</b><br>feature_id=%{{customdata[0]}}<br>log2FC=%{{x:.3f}}<br>-log10(p)=%{{y:.3f}}<br>FC=%{{customdata[1]:.3f}}<br>p=%{{customdata[2]:.3e}}<extra>" + label + "</extra>"
  }};
}}
function drawVolcano(resetView = false) {{
  const shown = volcanoDiffOnly ? volcano.filter(point => point.regulation !== "not_significant") : volcano;
  const traces = [
    volcanoTrace("up", "#c94132", "Up", shown.filter(point => point.regulation === "up")),
    volcanoTrace("down", "#2f6fab", "Down", shown.filter(point => point.regulation === "down")),
    ...(!volcanoDiffOnly ? [volcanoTrace("not_significant", "#9a9084", "Not significant", shown.filter(point => point.regulation === "not_significant"))] : [])
  ];
  const layout = {{
    ...plotPaper,
    margin:{{ l:86, r:18, t:12, b:78 }},
    dragmode:"pan",
    hovermode:"closest",
    showlegend:true,
    legend:{{ orientation:"h", y:1.12 }},
    xaxis:{{ title:{{ text:"log2 fold change", standoff:16 }}, automargin:true, zeroline:true, gridcolor:"#eadfce" }},
    yaxis:{{ title:{{ text:"-log10 p-value", standoff:16 }}, automargin:true, rangemode:"tozero", gridcolor:"#eadfce" }},
    uirevision: resetView ? String(Date.now()) : "volcano"
  }};
  Plotly.react("volcano", traces, layout, plotConfig);
}}
function drawHeatmap() {{
  const selection = document.getElementById('heatmapSelection');
  const search = document.getElementById('heatmapSearch');
  function renderRows() {{
    const query = search.value.trim().toLowerCase();
    const rows = heatmap.rows.filter(row => !query || `${{row.name}} ${{row.id}}`.toLowerCase().includes(query));
    const labels = rows.map(row => row.name || row.id);
    Plotly.react("heatmap", [{{
      type:"heatmap",
      x:heatmap.samples,
      y:labels,
      z:rows.map(row => row.values),
      customdata:rows.map(row => heatmap.samples.map(() => row.id)),
      zmin:-3,
      zmax:3,
      colorscale:[[0,"#2f6fab"],[.5,"#f7efe2"],[1,"#c94132"]],
      colorbar:{{ title:"row z-score" }},
      hovertemplate:"<b>%{{y}}</b><br>feature_id=%{{customdata}}<br>sample=%{{x}}<br>z-score=%{{z:.3f}}<extra></extra>"
    }}], {{
      ...plotPaper,
      margin:{{ l:180, r:18, t:12, b:72 }},
      xaxis:{{ title:"Sample", side:"bottom", automargin:true }},
      yaxis:{{ title:"Clustered protein axis", autorange:"reversed", automargin:true }},
      uirevision:"heatmap"
    }}, plotConfig);
    const plot = document.getElementById("heatmap");
    plot.removeAllListeners("plotly_click");
    plot.on("plotly_click", event => {{
      const point = event.points?.[0];
      if (!point) return;
    selection.innerHTML = `<strong>${{esc(point.y)}}</strong> | feature_id=${{esc(point.customdata)}} | sample=${{esc(point.x)}} | z-score=${{Number(point.z).toFixed(3)}}`;
    }});
  }}
  search.oninput = renderRows;
  renderRows();
}}
document.getElementById("volcanoAll").onclick = () => {{
  volcanoDiffOnly = false;
  document.getElementById("volcanoAll").classList.add("active");
  document.getElementById("volcanoDiff").classList.remove("active");
  drawVolcano();
}};
document.getElementById("volcanoDiff").onclick = () => {{
  volcanoDiffOnly = true;
  document.getElementById("volcanoDiff").classList.add("active");
  document.getElementById("volcanoAll").classList.remove("active");
  drawVolcano();
}};
document.getElementById("volcanoReset").onclick = () => drawVolcano(true);
drawVolcano(); drawHeatmap();
document.getElementById("volcano").on("plotly_click", event => {{
  const point = event.points?.[0];
  if (!point) return;
  const data = point.customdata;
  document.getElementById("volcanoSelection").innerHTML = `<strong>${{esc(point.text)}}</strong> | feature_id=${{esc(data[0])}} | log2FC=${{Number(point.x).toFixed(3)}} | FC=${{data[1] == null ? "NA" : Number(data[1]).toFixed(3)}} | p=${{Number(data[2]).toExponential(3)}} | ${{esc(data[3])}}`;
}});
</script>
</body>
</html>"""
