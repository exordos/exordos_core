SHELL := bash
ifeq ($(SSH_KEY),)
	SSH_KEY = ~/.ssh/id_rsa.pub
endif

all: help

help:
	@echo "build_core       - build exordos core"
	@echo "bootstrap        - bootstrap exordos core"

mdlint:
	markdownlint-cli2 --config .markdownlint.yaml "**/*.md" "#node_modules" "#!.venv" "#!.tox" --fix

build_core:
	exordos build -i $(SSH_KEY) -f

bootstrap:
	exordos bootstrap -i output -f -m core --admin-password admin --cidr 10.20.0.0/22

delete_core:
	exordos realms d local

add_ssh_keys:
	exordos secret ssh_keys add --current-realm --target_public_key $(SSH_KEY)

clear_ssh_keys:
	exordos secret ssh_keys clear
