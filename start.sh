#!/usr/bin/env bash
export MALLOC_ARENA_MAX=2
export PYTHONUNBUFFERED=1
streamlit run stock_pulse.py --server.port="${PORT:-8501}" --server.address=0.0.0.0 --server.headless=true
