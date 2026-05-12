SHELL := bash
REPOSITORY := https://repository.genesis-core.tech
ifeq ($(SSH_KEY),)
	SSH_KEY = ~/.ssh/id_rsa.pub
endif

all: help

help:
	@echo "build_core       - build exordos core"
	@echo "bootstrap        - bootstrap exordos core"

build_core:
	exordos build -i $(SSH_KEY) -f . --inventory --manifest-var repository=https://repository.genesis-core.tech

bootstrap:
	exordos bootstrap -i output/inventory.json -f -m core --admin-password admin --cidr 10.20.0.0/22

mdlint:
	markdownlint-cli2 --config .markdownlint.yaml "**/*.md" "#node_modules" --fix