#!/usr/bin/env bash
streamlit run stock_pulse.py --server.port="${PORT:-8501}" --server.address=0.0.0.0 --server.headless=true
