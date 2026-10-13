.PHONY: dev certs https

PYTHON := python
CERT_DIR := certs
CERT_FILE := $(CERT_DIR)/localhost.crt
KEY_FILE := $(CERT_DIR)/localhost.key

dev:
	$(PYTHON) manage.py runserver

certs:
	@if not exist "$(CERT_DIR)" mkdir "$(CERT_DIR)"
	mkcert -key-file "$(KEY_FILE)" -cert-file "$(CERT_FILE)" localhost 127.0.0.1 ::1

https: certs
	$(PYTHON) manage.py runserver_plus --cert-file "$(CERT_FILE)" --key-file "$(KEY_FILE)"