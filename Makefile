ENV_NAME=network

install:
	conda create -y -n $(ENV_NAME) python=3.9
	conda run -n $(ENV_NAME) pip install networkx matplotlib geoip2 pandas

run:
	chmod +x run_analyzer
	./run_analyzer

visualize:
	cd topology_visualizer && python3 run_visualizer.py enriched_results.json

all: run visualize

clean:
	rm -f topology_visualizer/sample_data.json topology_visualizer/enriched_results.json

help:
	@echo "Available targets:"
	@echo "  install  - create conda env and install Python deps"
	@echo "  all      - run analyzer, enrich data, and launch visualizer"
	@echo "  run      - rerun traceroute probing and enrichment"
	@echo "  visualize - launch visualizer using existing enriched_results.json (skips probing and enrichment)"
	@echo "  clean    - remove generated JSON files"