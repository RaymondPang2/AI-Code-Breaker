# Screenshots

Placeholder directory. The README references these files; capture them once the
stack is running locally and drop them in here with these exact names.

| File | What to capture | Notes |
|---|---|---|
| `01-submission-form.png` | The submission form with the built-in `second_largest` example loaded | Show both editors and the analysis toggles |
| `02-analysis-progress.png` | An analysis mid-flight | Catch a staged status (`executing_tests` or `minimizing`) with the progress bar visible |
| `03-results-overview.png` | The Overview tab of a completed run | Verdict plus pass/fail counts |
| `04-counterexample.png` | The Counterexample tab | Show the failing input `[5, 5, 5]` and both sides' results side by side |
| `05-all-tests.png` | The All Tests table | Have a filter applied so the pass/fail and source controls are obviously interactive |
| `06-ai-explanation.png` | The AI Explanation tab | Make sure the "advisory / not a verdict" labelling is legible — it's the point |
| `07-benchmark-run.png` | Terminal after `python -m benchmark.run --seed 0` | The summary block with detection rate and misses |

## Tips

- Capture at a consistent width (1440px works well) so the README table renders
  evenly.
- Use light mode unless the dark theme is more legible for the code editors.
- Crop out browser chrome, bookmarks, and anything identifying.
- **Check for secrets before committing** — no `.env` contents, API keys, or
  personal data in any frame.
