export type InventoryCoverage = {
  python_files_total_found?: number;
  python_files_analyzed?: number;
  python_files_truncated?: boolean;
  github_tree_truncated?: boolean;
  source_failures?: number;
  source_truncations?: number;
  unmatched_imports?: number;
};

export function inventoryStatus(coverage: InventoryCoverage = {}) {
  const count = (value: number | undefined) => Number.isInteger(value) && Number(value) >= 0 ? Number(value) : null;
  const found = count(coverage.python_files_total_found);
  const loaded = count(coverage.python_files_analyzed);
  const warnings: string[] = [];
  if (coverage.github_tree_truncated) warnings.push("GitHub returned an incomplete tree; the total file count is a lower bound.");
  if (found !== null && loaded !== null && found > loaded) warnings.push(`${found - loaded} discovered Python files were omitted by the hosted file limit.`);
  else if (coverage.python_files_truncated && !coverage.github_tree_truncated) warnings.push("The Python file inventory is incomplete.");
  if (coverage.source_failures) warnings.push(`${coverage.source_failures} file(s) could not be read; their outgoing imports were not analyzed.`);
  if (coverage.source_truncations) warnings.push(`${coverage.source_truncations} source excerpt(s) were truncated; imports beyond the excerpt were not analyzed.`);
  if (coverage.unmatched_imports) warnings.push(`${coverage.unmatched_imports} scanned import reference(s) could not be matched uniquely. They may be external, ambiguous, or outside this inventory.`);
  return {
    partial: Boolean(coverage.python_files_truncated || coverage.github_tree_truncated || coverage.source_failures || coverage.source_truncations),
    summary: loaded !== null && found !== null ? `${loaded} of ${coverage.github_tree_truncated ? "at least " : ""}${found} discovered Python files inventoried` : "Inventory coverage unavailable",
    warnings,
  };
}

export function inventoryUnavailable(view: "patterns" | "risks" | "flows") {
  return {
    title: `${{ patterns: "Architecture patterns", risks: "Risks and remediation", flows: "Execution flows" }[view]} not analyzed`,
    detail: "Inventory mode does not run this analysis. An empty result is not evidence of absence. Use the full Python analyzer for this view.",
  };
}
