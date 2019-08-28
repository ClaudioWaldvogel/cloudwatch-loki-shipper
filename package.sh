#!/usr/bin/env bash
CURRENT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
TARGET_DIR=${CURRENT}/target
SHIPPER_DIR=${CURRENT}/shipper
PACKAGE_DIR=$(find ${CURRENT}/venv -name "site-packages")
DEMO_DIR=${CURRENT}/demo

mkdir ${TARGET_DIR}

echo "Create Shipper deployment package"
cd ${PACKAGE_DIR}
zip -r9 ${TARGET_DIR}/shipper.zip *
cd ${SHIPPER_DIR}
zip -g ${TARGET_DIR}/shipper.zip  *

echo "Create demo lambda deployment package"
cd ${DEMO_DIR}
zip -r9 ${TARGET_DIR}/demo-lambda.zip demo-lambda.py

