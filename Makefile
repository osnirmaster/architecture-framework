.PHONY: install fitness-validate fitness-all test validate check publish serve clean

install:
	python3 -m pip install -r requirements.txt

fitness-validate:
	python3 scripts/fitness_engine.py validate-definitions

fitness-all:
	python3 scripts/fitness_engine.py evaluate --graph dist/graph/graph.json

test:
	python3 -m unittest discover -s tests -v

validate: fitness-validate test
	python3 scripts/harness.py validate

check: fitness-validate test
	python3 scripts/harness.py check

publish: validate
	python3 scripts/harness.py publish

serve:
	python3 -m http.server 8080

clean:
	rm -rf workspace/harness/* workspace/fitness/results/* dist/graph/*
