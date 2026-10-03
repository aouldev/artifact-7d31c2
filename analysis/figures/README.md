# Result figure inputs

This directory contains small, dependency-free renderers for the numerical
evidence that is currently released with the artifact. They read only the
public files under `results/` and write SVG previews to a caller-selected
output directory.

From the artifact root, run:

```bash
python analysis/figures/plot_characterization.py --output-dir /tmp/repotem-figures
python analysis/figures/plot_locator.py --output-dir /tmp/repotem-figures
```

The characterization renderer uses `results/benchmark_characterization/condition_coverage.json`
and `maintenance_pattern_summary.json`. The locator renderer uses
`results/locator/metrics.json`. Each renderer validates the counts and
denominators before writing its SVG output.

The scripts provide compact, data-backed previews and an auditable mapping
from released result files to plots. Narrative diagrams and code-excerpt
figures are maintained separately from these numerical renderers. Additional
RQ-specific figures will be added only after their source data and renderers
have been reviewed.
