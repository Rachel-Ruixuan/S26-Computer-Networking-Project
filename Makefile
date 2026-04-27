ENV_NAME=network

# default flags (can be overridden from CLI)
SKIP_INSTALL ?= 0
SKIP_RUN ?= 0
SKIP_VIS ?= 0

pipeline:
ifeq ($(SKIP_INSTALL),0)
	@echo ">>> Installing environment..."
	conda create -y -n $(ENV_NAME) python=3.9
	conda run -n $(ENV_NAME) pip install networkx matplotlib geoip2 pandas
else
	@echo ">>> Skipping install"
endif

ifeq ($(SKIP_RUN),0)
	@echo ">>> Running analyzer..."
	chmod +x run_analyzer
	./run_analyzer
else
	@echo ">>> Skipping analyzer"
endif

ifeq ($(SKIP_VIS),0)
	@echo ">>> Launching visualizer..."
	cd topology_visualizer && python3 run_visualizer.py enriched_results.json
else
	@echo ">>> Skipping visualization"
endif

install:
	$(MAKE) pipeline SKIP_RUN=1 SKIP_VIS=1

run:
	$(MAKE) pipeline SKIP_INSTALL=1 SKIP_VIS=1

visualize:
	$(MAKE) pipeline SKIP_INSTALL=1 SKIP_RUN=1

all:
	$(MAKE) pipeline

clean:
	rm -f topology_visualizer/sample_data.json topology_visualizer/enriched_results.json

help:
	@echo "Usage:"
	@echo "  make pipeline [SKIP_INSTALL=1] [SKIP_RUN=1] [SKIP_VIS=1]"
	@echo ""
	@echo "Examples:"
	@echo "  make pipeline                         # full run (install + run + visualize)"
	@echo "  make pipeline SKIP_INSTALL=1         # skip install"
	@echo "  make pipeline SKIP_RUN=1             # skip probing"
	@echo "  make pipeline SKIP_RUN=1 SKIP_INSTALL=1  # only visualize"