# HOW TO INSTALL AND RUN

## Quick Start (Recommended)

Run the full pipeline (install → probe → visualize):

```bash
make pipeline
```

This will automatically:

- create the Conda environment
- run traceroute probing
- generate `sample_data.json`
- download GeoLite2 databases (if needed)
- enrich data with geolocation and ASN
- produce `enriched_results.json`
- launch the visualizer


## Reuse Existing Probing Data (Fastest)

The repository already includes:

```
topology_visualizer/enriched_results.json
```

To skip probing and directly launch the visualizer:

```bash
make pipeline SKIP_RUN=1
```


## Common Usage

Run specific parts of the pipeline:

```bash
# Install environment only
make pipeline SKIP_RUN=1 SKIP_VIS=1

# Re-run probing + enrichment only
make pipeline SKIP_INSTALL=1 SKIP_VIS=1

# Visualize existing results
make pipeline SKIP_INSTALL=1 SKIP_RUN=1
```

## Notes

- Root privileges may be required for traceroute probing.
- First run may take longer due to dependency installation and GeoLite2 download.

### Output Files

- `sample_data.json` — raw traceroute results  
- `enriched_results.json` — enriched data used by the visualizer  
