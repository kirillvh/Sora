## How to View Costs
python -m Sora.Ledger.report_stats

### Recalibrate tokenizer costs if you change LLM_MODEL
python -m Sora.Ledger.calibrate

## Calibrate Judges (change labeller name)
python -m Sora.Judges.Calibration --n 20 --labeller kirill

## Compare Baseline Persona to Improved persona using calibrated Judess
python -m Sora.Judges.Benchmark --repeats 3 --max-usd 0.50
Report lands in: out\benchmark\benchmark_report.md after running calibration.

## How to benchmark Compaction and verify Sora's voice integrity
python -m Sora.Compaction.verify --repeats 3
Report in: out\benchmark\compaction_report.md