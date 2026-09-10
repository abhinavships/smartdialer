DB_URL ?= postgresql+asyncpg://smartdialer:smartdialer@localhost:5432/smartdialer
export DATABASE_URL = $(DB_URL)

.PHONY: install test sim loadtest

install:
	pip install -r requirements.txt

test:
	pytest -q

# sim / loadtest wired up in later build phases.
