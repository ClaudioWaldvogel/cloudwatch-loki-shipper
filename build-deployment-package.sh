#!/usr/bin/env bash
CURRENT=`pwd`
cd venv/lib/python3.7/site-packages
zip -r9 ${CURRENT}/lambda-loki-logging.zip .
cd ${CURRENT}
zip -g lambda-loki-logging.zip scripts/*