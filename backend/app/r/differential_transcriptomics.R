args <- commandArgs(trailingOnly = TRUE)
matrix_file <- args[[1]]
metadata_file <- args[[2]]
comparisons_file <- args[[3]]
output_dir <- args[[4]]
padj_cutoff <- as.numeric(args[[5]])
log2_fc_cutoff <- as.numeric(args[[6]])

if (!requireNamespace("DESeq2", quietly = TRUE)) {
  stop("DESeq2 package is required for transcriptomics differential analysis.")
}
suppressPackageStartupMessages(library(DESeq2))

matrix <- read.csv(matrix_file, check.names = FALSE, stringsAsFactors = FALSE)
metadata <- read.csv(metadata_file, check.names = FALSE, stringsAsFactors = FALSE)
comparisons <- read.csv(comparisons_file, check.names = FALSE, stringsAsFactors = FALSE)
samples <- metadata$sample[metadata$sample %in% colnames(matrix)]
metadata <- metadata[match(samples, metadata$sample), , drop = FALSE]
rownames(metadata) <- metadata$sample
metadata$condition <- factor(metadata$condition)

raw_counts <- matrix[, samples, drop = FALSE]
raw_counts[] <- lapply(raw_counts, function(x) round(suppressWarnings(as.numeric(as.character(x)))))
counts_matrix <- as.matrix(raw_counts)
rownames(counts_matrix) <- make.unique(as.character(matrix$feature_id))
storage.mode(counts_matrix) <- "integer"

dds <- DESeqDataSetFromMatrix(
  countData = counts_matrix,
  colData = metadata,
  design = ~ condition
)
keep <- rowSums(counts(dds) >= 10) >= min(3, ncol(dds))
dds <- dds[keep, ]
if (nrow(dds) == 0) {
  stop("No genes remain after the low-count filter.")
}
dds <- DESeq(dds, fitType = "mean", minReplicatesForReplace = 7, parallel = FALSE)

normalized <- as.data.frame(counts(dds, normalized = TRUE))
normalized$gene_id <- rownames(normalized)
normalized <- normalized[, c("gene_id", samples), drop = FALSE]
write.csv(normalized, file.path(output_dir, "normalized_counts.csv"), row.names = FALSE, na = "")

for (i in seq_len(nrow(comparisons))) {
  numerator <- comparisons$numerator[[i]]
  denominator <- comparisons$denominator[[i]]
  slug <- comparisons$slug[[i]]
  if (!(numerator %in% levels(metadata$condition)) || !(denominator %in% levels(metadata$condition))) {
    stop(paste("Unknown comparison groups:", numerator, denominator))
  }
  res <- results(
    dds,
    contrast = c("condition", numerator, denominator),
    alpha = padj_cutoff,
    independentFiltering = TRUE
  )
  res_df <- as.data.frame(res)
  res_df$gene_id <- rownames(res_df)
  res_df <- res_df[!is.na(res_df$padj) & !is.na(res_df$log2FoldChange), , drop = FALSE]
  res_df$regulation <- "not_significant"
  res_df$regulation[res_df$padj < padj_cutoff & res_df$log2FoldChange >= log2_fc_cutoff] <- "up"
  res_df$regulation[res_df$padj < padj_cutoff & res_df$log2FoldChange <= -log2_fc_cutoff] <- "down"
  res_df <- res_df[order(res_df$padj, -abs(res_df$log2FoldChange)), , drop = FALSE]
  significant <- res_df[res_df$regulation != "not_significant", , drop = FALSE]
  write.csv(res_df, file.path(output_dir, paste0(slug, "_all_genes.csv")), row.names = FALSE, na = "")
  write.csv(significant, file.path(output_dir, paste0(slug, "_significant_genes.csv")), row.names = FALSE, na = "")
  write.csv(significant[significant$regulation == "up", , drop = FALSE], file.path(output_dir, paste0(slug, "_up_genes.csv")), row.names = FALSE, na = "")
  write.csv(significant[significant$regulation == "down", , drop = FALSE], file.path(output_dir, paste0(slug, "_down_genes.csv")), row.names = FALSE, na = "")
}
