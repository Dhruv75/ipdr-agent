.PHONY: help install data test eval lint run docker-build docker-up clean

help:
	@echo "Targets:"
	@echo "  install       Install runtime + dev dependencies"
	@echo "  data          Generate the synthetic IPDR dataset (~5k rows)"
	@echo "  test          Run the unit test suite"
	@echo "  eval          Run the dual-engine evaluation harness (CI gate)"
	@echo "  lint          Run ruff"
	@echo "  run           Launch the Streamlit app"
	@echo "  docker-up     Build and run app + Qdrant via docker compose"

install:
	pip install -r requirements-dev.txt

data:
	python scripts/generate_data.py --rows 5000 --out data/rag_formatted_data.xlsx

test:
	PYTHONPATH=src pytest

eval:
	PYTHONPATH=src python eval/run_eval.py

lint:
	ruff check src tests eval scripts

run:
	streamlit run app/streamlit_app.py

docker-build:
	docker build -t ipdr-forensic-agent .

docker-up:
	docker compose up --build

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache *.egg-info
