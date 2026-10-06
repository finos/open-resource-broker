# AWS resource history visualiser

Processes history files produced by AWS Auto Scaling Group, EC2 Fleet, and
Spot Fleet operations, extracting timing and capacity metrics into CSV,
JSON, Parquet, or Excel reports. When given multiple test folders it can
also render a cumulative plot comparing capacity ramp-up across runs.

## Usage

```bash
python visualise.py history.json output.csv
python visualise.py --provider-type ASG history.json output.csv
python visualise.py --output-format json history.json output.json
python visualise.py --validate-only history.json
python visualise.py --expand-dirs --cumulative-plot test-run-a test-run-b
```

Run `python visualise.py --help` for the full option list.

## Dependencies

This tool is not wired into ORB's own dependency set or `uv.lock`; install
its requirements into your own environment before running it:

```bash
pip install -r requirements.txt
```

`boto3` is also required but is already part of ORB's own dependencies, so
it is not listed separately here.
