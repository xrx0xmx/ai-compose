ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
OPS_SCRIPT := $(ROOT_DIR)/scripts/ops.sh

TARGET ?= all
TAIL ?= 200
MODEL ?=
MODE ?=
TTL ?= 45

-include Makefile.ops

help:
	@echo "Comandos de produccion:"
	@echo "  make up"
	@echo "  make down"
	@echo "  make deploy"
	@echo "  make ps"
	@echo "  make logs [TARGET=all|servicio|contenedor] [TAIL=200]"
	@echo "  make status"
	@echo "  make test"
	@echo "  make switch MODEL=<id-en-/models>"
	@echo "  make mode MODE=comfy [TTL=45]"
	@echo "  make mode MODE=llm [MODEL=qwen-fast]"
	@echo "  make doctor"
	@echo "  make pull"
	@echo ""
	@echo "Operaciones host:"
	@echo "  make vpn-up | make vpn-down | make vpn-status | make ssh"
	@echo "  make scp-home [SCP_SRC=.] [SCP_DEST=~/]"

up:      ; @$(OPS_SCRIPT) up

down:    ; @$(OPS_SCRIPT) down

deploy:  ; @$(OPS_SCRIPT) deploy

ps:      ; @$(OPS_SCRIPT) ps

pull:    ; @$(OPS_SCRIPT) pull

logs:    ; @$(OPS_SCRIPT) logs "$(TARGET)" "$(TAIL)"

status:  ; @$(OPS_SCRIPT) status

test:    ; @$(OPS_SCRIPT) test

doctor:  ; @$(OPS_SCRIPT) doctor

switch:
	@test -n "$(MODEL)" || { echo "ERROR: define MODEL=<id-en-/models>"; exit 1; }
	@$(OPS_SCRIPT) switch "$(MODEL)"

mode:
	@test -n "$(MODE)" || { echo "ERROR: define MODE=llm|comfy"; exit 1; }
	@$(OPS_SCRIPT) mode "$(MODE)" "$(MODEL)" "$(TTL)"

.PHONY: help up down deploy ps pull logs status test doctor switch mode
