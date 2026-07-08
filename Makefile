.PHONY: install install-dev test lint run build clean

install:
	pip install -r requirements.txt

install-dev: install
	pip install -r requirements-dev.txt

test:
	pytest tests/ -v

lint:
	python -m pyflakes core.py gui.py grab_subs.py tests/

run:
	python gui.py

build:
	pyinstaller YouScriber.spec

clean:
	rm -rf build dist __pycache__ .pytest_cache
