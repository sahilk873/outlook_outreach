#!/bin/bash
# Validate the pipeline with a single email (config: outreach.validate.yaml)
cd "$(dirname "$0")"
python main.py -c outreach.validate.yaml
