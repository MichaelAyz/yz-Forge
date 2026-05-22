#!/bin/bash

BASE="http://localhost:8000"

echo ""
echo "════════════════════════════════"
echo "  FORGE ENGINE TEST SUITE"
echo "════════════════════════════════"


# ── Helper functions ──────────────────────────────────────

submit_pipeline() {
    local file=$1
    local response=$(curl -s -X POST "$BASE/runs" \
        -F "pipeline=@$file")
    echo $response
}

get_run() {
    local run_id=$1
    curl -s "$BASE/runs/$run_id"
}

wait_for_run() {
    local run_id=$1
    local max_wait=60
    local waited=0
    while [ $waited -lt $max_wait ]; do
        status=$(curl -s "$BASE/runs/$run_id" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
        if [ "$status" != "queued" ] && [ "$status" != "running" ]; then
            echo $status
            return
        fi
        sleep 2
        waited=$((waited + 2))
    done
    echo "timeout"
}


# ── Test 1: Health check ─────────────────────────────────

echo ""
echo "TEST 1: Health check"
result=$(curl -s "$BASE/health")
echo "Response: $result"


# ── Test 2: Simple pipeline ──────────────────────────────

echo ""
echo "TEST 2: Simple pipeline (should succeed)"
response=$(submit_pipeline "test_pipelines/simple.yaml")
echo "Submitted: $response"
run_id=$(echo $response | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "Run ID: $run_id"
echo "Waiting for completion..."
final_status=$(wait_for_run $run_id)
echo "Final status: $final_status"
if [ "$final_status" = "succeeded" ]; then
    echo "✅ PASSED"
else
    echo "❌ FAILED — expected succeeded, got $final_status"
fi


# ── Test 3: Parallel jobs ────────────────────────────────

echo ""
echo "TEST 3: Parallel jobs (job-c waits for job-a and job-b)"
response=$(submit_pipeline "test_pipelines/parallel.yaml")
run_id=$(echo $response | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "Run ID: $run_id"
final_status=$(wait_for_run $run_id)
echo "Final status: $final_status"
if [ "$final_status" = "succeeded" ]; then
    echo "✅ PASSED"
else
    echo "❌ FAILED — expected succeeded, got $final_status"
fi


# ── Test 4: Cycle detection ──────────────────────────────

echo ""
echo "TEST 4: Cycle detection (should reject with 422)"
response=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/runs" \
    -F "pipeline=@test_pipelines/cycle.yaml")
echo "HTTP status: $response"
if [ "$response" = "422" ]; then
    echo "✅ PASSED"
else
    echo "❌ FAILED — expected 422, got $response"
fi


# ── Test 5: Unknown fields ───────────────────────────────

echo ""
echo "TEST 5: Unknown fields in YAML (should reject with 422)"
response=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/runs" \
    -F "pipeline=@test_pipelines/bad-fields.yaml")
echo "HTTP status: $response"
if [ "$response" = "422" ]; then
    echo "✅ PASSED"
else
    echo "❌ FAILED — expected 422, got $response"
fi


# ── Test 6: Log streaming ────────────────────────────────

echo ""
echo "TEST 6: Log streaming"
response=$(submit_pipeline "test_pipelines/simple.yaml")
run_id=$(echo $response | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "Run ID: $run_id"
echo "Streaming logs (5 seconds):"
timeout 5 curl -s "$BASE/runs/$run_id/logs?follow=true" || true
echo ""
echo "✅ Log stream test done"


# ── Test 7: Filesystem isolation ─────────────────────────

echo ""
echo "TEST 7: Filesystem isolation"
response=$(submit_pipeline "test_pipelines/escape.yaml")
run_id=$(echo $response | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "Run ID: $run_id"
final_status=$(wait_for_run $run_id)
echo "Final status: $final_status"
echo "Check logs to verify host FS was not accessible:"
curl -s "$BASE/runs/$run_id/logs" | head -20
echo ""
echo "✅ Isolation test done — review logs above"


echo ""
echo "════════════════════════════════"
echo "  ALL TESTS COMPLETE"
echo "════════════════════════════════"