args <- commandArgs(trailingOnly = TRUE)
matrix_file <- args[[1]]
metadata_file <- args[[2]]
comparisons_file <- args[[3]]
output_dir <- args[[4]]
pvalue_cutoff <- as.numeric(args[[5]])
fold_change_cutoff <- as.numeric(args[[6]])

matrix <- read.csv(matrix_file, check.names = FALSE, stringsAsFactors = FALSE)
metadata <- read.csv(metadata_file, check.names = FALSE, stringsAsFactors = FALSE)
comparisons <- read.csv(comparisons_file, check.names = FALSE, stringsAsFactors = FALSE)

safe_num <- function(x) suppressWarnings(as.numeric(gsub(",", "", as.character(x))))

run_comparison <- function(numerator, denominator) {
  samples_numerator <- metadata$sample[metadata$condition == numerator]
  samples_denominator <- metadata$sample[metadata$condition == denominator]
  if (length(samples_numerator) < 2 || length(samples_denominator) < 2) {
    stop(paste("Each comparison group needs at least two samples:", numerator, denominator))
  }

  rows <- vector("list", nrow(matrix))
  for (i in seq_len(nrow(matrix))) {
    values_numerator <- safe_num(unlist(matrix[i, samples_numerator, drop = FALSE]))
    values_denominator <- safe_num(unlist(matrix[i, samples_denominator, drop = FALSE]))
    values_numerator <- values_numerator[is.finite(values_numerator)]
    values_denominator <- values_denominator[is.finite(values_denominator)]
    mean_numerator <- if (length(values_numerator) > 0) mean(values_numerator) else NA
    mean_denominator <- if (length(values_denominator) > 0) mean(values_denominator) else NA
    fc <- if (is.finite(mean_denominator) && mean_denominator > 0 && is.finite(mean_numerator)) {
      mean_numerator / mean_denominator
    } else {
      NA
    }
    log2_fc <- if (is.finite(fc) && fc > 0) log2(fc) else NA
    pvalue <- NA
    if (length(values_numerator) >= 2 && length(values_denominator) >= 2) {
      pvalue <- tryCatch(t.test(values_numerator, values_denominator)$p.value, error = function(e) NA)
    }
    rows[[i]] <- data.frame(
      feature_id = matrix$feature_id[[i]],
      feature_name = matrix$feature_name[[i]],
      description = matrix$description[[i]],
      numerator = numerator,
      denominator = denominator,
      mean_numerator = mean_numerator,
      mean_denominator = mean_denominator,
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
  result[order(result$pvalue, na.last = TRUE), ]
}

for (i in seq_len(nrow(comparisons))) {
  numerator <- comparisons$numerator[[i]]
  denominator <- comparisons$denominator[[i]]
  slug <- comparisons$slug[[i]]
  if (!(numerator %in% metadata$condition) || !(denominator %in% metadata$condition)) {
    stop(paste("Unknown comparison groups:", numerator, denominator))
  }
  result <- run_comparison(numerator, denominator)
  differential <- result[result$regulation != "not_significant", , drop = FALSE]
  write.csv(result, file.path(output_dir, paste0(slug, "_all_results.csv")), row.names = FALSE, na = "")
  write.csv(differential, file.path(output_dir, paste0(slug, "_differential_results.csv")), row.names = FALSE, na = "")
  write.csv(differential[differential$regulation == "up", , drop = FALSE], file.path(output_dir, paste0(slug, "_up_results.csv")), row.names = FALSE, na = "")
  write.csv(differential[differential$regulation == "down", , drop = FALSE], file.path(output_dir, paste0(slug, "_down_results.csv")), row.names = FALSE, na = "")
}
