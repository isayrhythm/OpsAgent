---
name: blast_query
version: 1
description: 使用本地植物 BLAST 数据库比对用户粘贴或上传的 DNA、RNA、蛋白质 FASTA 序列，返回拟南芥、玉米、水稻或大豆中的候选同源基因/蛋白记录。
trigger: BLAST、blastn、blastp、blastx、tblastn、序列比对、同源基因、同源蛋白、查找这段序列对应的基因、这条序列属于哪个物种、FASTA 比对、核酸序列比对、蛋白序列比对；用户粘贴 FASTA 或上传 .fa/.fasta/.fna/.faa 文件并要求比对时使用。
execution_mode: deterministic_python
executor: blast_query
argument_resolver: message
input_schema: skill/schemas/blast_query.input.json
output_schema: skill/schemas/blast_query.output.json
data_paths: data/blast_db/Arabidopsis/nt, data/blast_db/Arabidopsis/protein, data/blast_db/Maize/nt, data/blast_db/Maize/protein, data/blast_db/Rice/nt, data/blast_db/Rice/protein, data/blast_db/Soybean/nt, data/blast_db/Soybean/protein
---

# BLAST Query Skill

## Contract

- 输入可以是用户在对话中粘贴的裸序列、单条或多条 FASTA，也可以是已上传的 `.fa`、`.fasta`、`.fna`、`.faa`、`.fas` 文件。
- 每次最多处理 10 条序列。多序列按程序分组后批量执行，不会拆成每条序列独立任务。
- 未指定物种时，并行搜索 Arabidopsis、maize、rice、soybean 四个本地数据库。
- 默认每条 query 返回 top 5 hits，用户可要求 `top N`，最大为 10。
- 默认 e-value 阈值为 `1e-10`，可由用户指定。

## Program Selection

- DNA/RNA 默认使用 `blastn` 搜核酸库。
- 蛋白质默认使用 `blastp` 搜蛋白库。
- 用户明确要求翻译搜索时，可使用 `blastx` 或 `tblastn`。
- RNA 会在执行前将 `U` 转换为 `T`。

## Result Semantics

- `subject_id` 是本地 FASTA header 对应的命中记录 ID。
- 核酸库中的命中通常可视为候选同源基因记录。
- 蛋白库中的命中可能是蛋白或转录本记录，不要强行宣称一定是标准基因 ID。
- 返回 identity、query coverage、subject coverage、e-value、bit score、HSP 数量和坐标。
- 多条 FASTA 的结果必须按 `query_label` 分组展示，保留 `>` 标签对应关系。
- 对每条返回的 BLAST hit，使用 `data/gene_trans/*_gene_trans.json` 解析标准基因 ID，并从对应 `data/gene_info/*_gene_info.json` 查询精简功能摘要。
- 基因信息 enrichment 只返回 canonical ID 和精简功能摘要，不要返回 gene info 原始大文本块，以免撑大上下文。

## Constraints

- 不要编造命中记录、功能注释或物种。
- 无命中时直接说明当前 e-value 阈值下没有结果。
- 序列超过 10000 residues、包含截断占位符、无法判断类型或超过 10 条时，应提示用户修正输入。
