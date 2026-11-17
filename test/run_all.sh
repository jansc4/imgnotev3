#!/bin/bash
# Wszystkie testy jednostkowe po kolei: bash test/run_all.sh
# (E2E osobno: QT_QPA_PLATFORM=offscreen uv run python test/e2e_offscreen.py)
cd "$(dirname "$0")/.." || exit 1
status=0
run() { if uv run python "$1" > /tmp/imgnote_test.log 2>&1; then echo "OK   $1"; else echo "FAIL $1"; tail -8 /tmp/imgnote_test.log; status=1; fi; }
run test/test_image_note_file_v4.py
run test/test_index_file.py
run test/test_index_reconcile.py
run test/test_index_manager.py
run test/test_folder_manager.py
run test/test_note_cache.py
run test/test_thumbnail.py
run test/test_search_engine.py
run test/test_atomic_and_header.py
run test/test_render_markdown.py
run test/test_api_paths.py
run test/test_api_stage2.py
run test/test_migration_v3_to_v4.py
run test/smoke_test_api.py
exit $status
