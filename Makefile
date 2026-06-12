SHELL := bash
ifeq ($(SSH_KEY),)
	SSH_KEY = ~/.ssh/id_rsa.pub
endif

all: help

help:
	@echo "build_core       - build exordos core"
	@echo "bootstrap        - bootstrap exordos core"

build_core:
	exordos build -i $(SSH_KEY) -f

bootstrap:
	exordos bootstrap -i output -f -m core --admin-password admin --cidr 10.20.0.0/22

delete_core:
	exordos realms d local

mdlint:
	markdownlint-cli2 --config .markdownlint.yaml "**/*.md" "#node_modules" "#!.venv" "#!.tox" --fix