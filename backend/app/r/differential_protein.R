args <- commandArgs(trailingOnly = TRUE)
matrix_file <- args[[1]]
metadata_file <- args[[2]]
output_dir <- args[[3]]
group_a <- args[[4]]
group_b <- args[[5]]
pvalue_cutoff <- as.numeric(args[[6]])
fold_change_cutoff <- as.numeric(args[[7]])

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
result$regulation[is.finite(result$fold_change) & is.finite(result$pvalue) & result$fold_change >= fold_change_cutoff & result$pvalue < pvalue_cutoff] <- "up"
result$regulation[is.finite(result$fold_change) & is.finite(result$pvalue) & result$fold_change <= (1 / fold_change_cutoff) & result$pvalue < pvalue_cutoff] <- "down"
result <- result[order(result$pvalue, na.last = TRUE), ]

write.csv(result, file.path(output_dir, "all_results.csv"), row.names = FALSE, na = "")
write.csv(result[result$regulation != "not_significant", ], file.path(output_dir, "differential_results.csv"), row.names = FALSE, na = "")
write.csv(result[result$regulation == "up", ], file.path(output_dir, "up_results.csv"), row.names = FALSE, na = "")
write.csv(result[result$regulation == "down", ], file.path(output_dir, "down_results.csv"), row.names = FALSE, na = "")
