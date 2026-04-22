# Internet Topology Explorer — simplified, scalable visualizer

This version is designed for larger traceroute result files.

## What changed

- Removed diamond-related metrics from the UI.
- Tree view now automatically aggregates dense multi-destination outputs.
- Map view now uses geographic bins instead of plotting every hop individually.
- Added protocol comparison charts and protocol statistics table.
- Added most-shared-hops table.
- Added paginated raw probe table.
- Map view draws a world-style background even when no geolocation data is present.

## Expected JSON input

The visualizer reads the traceroute result JSON from `run_visualizer.py` and expects:

- `source`
- `destinations[]`
  - `target`
  - `probes[]`
    - `ttl`
    - `series`
    - `protocol`
    - `success`
    - `reply_ip`
    - `rtt_ms`
    - optional `reached_destination`
    - optional geolocation fields for map mode:
      - `lat`
      - `lng`
      - `city`
      - `country`

## Run

```bash
python3 run_visualizer.py sample_data.json
```

## Notes

- If many destinations are selected, tree view shows a shared-path summary rather than every node.
- If geolocation is missing from the JSON, the world map background is shown but no hops are placed.
