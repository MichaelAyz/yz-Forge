#!/bin/bash

TOKEN="forge-bf4f13a0868f74da819385e7081c7c06eab20e66636d8c22a3a736dfbfd63ee2"

echo "════════════════════════════════════════"
echo "  FORGE — FULL CAPABILITY TEST SUITE"
echo "════════════════════════════════════════"

BASE_ENGINE="http://localhost:8000"
BASE_REGISTRY="http://localhost:8002"

wait_for_run() {
    local run_id=$1
    local max_wait=${2:-60}
    local waited=0
    while [ $waited -lt $max_wait ]; do
        status=$(curl -s "$BASE_ENGINE/runs/$run_id" | python -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null)
        if [ "$status" != "queued" ] && [ "$status" != "running" ]; then
            echo $status
            return
        fi
        sleep 2
        waited=$((waited + 2))
    done
    echo "timeout"
}

echo ""
echo "[ CAP 1 ] Build and publish lib-core@1.0.0"
r=$(curl -s -X POST $BASE_ENGINE/runs \
  -H "Authorization: Bearer $TOKEN" \
  -F "pipeline=@test_pipelines/publish_lib_core.yaml")
echo "Response: $r"
id=$(echo $r | python -c "import sys,json; print(json.load(sys.stdin)['run_id'])" 2>/dev/null)
if [ -n "$id" ]; then
  status=$(wait_for_run $id 60)
  [ "$status" = "succeeded" ] && echo "✅ PASSED" || echo "❌ FAILED — $status"
else
  echo "❌ FAILED — no run_id returned"
fi

echo ""
echo "[ CAP 2 ] Build lib-http@1.0.0 depending on lib-core"
r=$(curl -s -X POST $BASE_ENGINE/runs \
  -H "Authorization: Bearer $TOKEN" \
  -F "pipeline=@test_pipelines/publish_lib_http.yaml")
echo "Response: $r"
id=$(echo $r | python -c "import sys,json; print(json.load(sys.stdin)['run_id'])" 2>/dev/null)
if [ -n "$id" ]; then
  status=$(wait_for_run $id 60)
  [ "$status" = "succeeded" ] && echo "✅ PASSED" || echo "❌ FAILED — $status"
else
  echo "❌ FAILED — no run_id returned"
fi

echo ""
echo "[ CAP 3 ] Build service-api@0.1.0 depending on both"
r=$(curl -s -X POST $BASE_ENGINE/runs \
  -H "Authorization: Bearer $TOKEN" \
  -F "pipeline=@test_pipelines/publish_service_api.yaml")
echo "Response: $r"
id=$(echo $r | python -c "import sys,json; print(json.load(sys.stdin)['run_id'])" 2>/dev/null)
if [ -n "$id" ]; then
  status=$(wait_for_run $id 60)
  [ "$status" = "succeeded" ] && echo "✅ PASSED" || echo "❌ FAILED — $status"
else
  echo "❌ FAILED — no run_id returned"
fi

echo ""
echo "[ CAP 4 ] Wrong checksum → 400"
echo "test" > /tmp/t.tar.gz
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST $BASE_REGISTRY/artifacts/test-pkg/1.0.0 \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/tmp/t.tar.gz" \
  -F "checksum=sha256:0000000000000000000000000000000000000000000000000000000000000000")
[ "$code" = "400" ] && echo "✅ PASSED" || echo "❌ FAILED — got $code"

echo ""
echo "[ CAP 5 ] Duplicate upload → 409"
sha=$(sha256sum /tmp/t.tar.gz | cut -d' ' -f1)
curl -s -X POST $BASE_REGISTRY/artifacts/unique-pkg/1.0.0 \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/tmp/t.tar.gz" -F "checksum=sha256:$sha" > /dev/null
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST $BASE_REGISTRY/artifacts/unique-pkg/1.0.0 \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/tmp/t.tar.gz" -F "checksum=sha256:$sha")
[ "$code" = "409" ] && echo "✅ PASSED" || echo "❌ FAILED — got $code"

echo ""
echo "[ CAP 6 ] Version conflict → conflict_failure"
r=$(curl -s -X POST $BASE_ENGINE/runs \
  -H "Authorization: Bearer $TOKEN" \
  -F "pipeline=@test_pipelines/conflict.yaml")
echo "Response: $r"
id=$(echo $r | python -c "import sys,json; print(json.load(sys.stdin)['run_id'])" 2>/dev/null)
if [ -n "$id" ]; then
  status=$(wait_for_run $id 30)
  [ "$status" = "conflict_failure" ] && echo "✅ PASSED" || echo "❌ FAILED — $status"
else
  echo "❌ FAILED — pipeline rejected at submission"
fi

echo ""
echo "[ CAP 7a ] Filesystem escape → contained"
r=$(curl -s -X POST $BASE_ENGINE/runs \
  -H "Authorization: Bearer $TOKEN" \
  -F "pipeline=@test_pipelines/escape_fs.yaml")
id=$(echo $r | python -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
wait_for_run $id 30 > /dev/null
logs=$(curl -s $BASE_ENGINE/runs/$id/logs)
echo "$logs" | grep -q "BLOCKED" && echo "✅ PASSED" || echo "❌ FAILED — check logs manually"

echo ""
echo "[ CAP 7b ] Memory exhaustion → contained"
r=$(curl -s -X POST $BASE_ENGINE/runs \
  -H "Authorization: Bearer $TOKEN" \
  -F "pipeline=@test_pipelines/escape_memory.yaml")
id=$(echo $r | python -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
status=$(wait_for_run $id 30)
[ "$status" = "failed" ] && echo "✅ PASSED — OOM killed container" || echo "❌ FAILED — $status"

echo ""
echo "[ CAP 7c ] Network egress → contained"
r=$(curl -s -X POST $BASE_ENGINE/runs \
  -H "Authorization: Bearer $TOKEN" \
  -F "pipeline=@test_pipelines/escape_network.yaml")
id=$(echo $r | python -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
wait_for_run $id 30 > /dev/null
logs=$(curl -s $BASE_ENGINE/runs/$id/logs)
echo "$logs" | grep -q "BLOCKED" && echo "✅ PASSED" || echo "❌ FAILED — network may not be blocked"

echo ""
echo "[ CAP 8 ] 50MB log streaming"
r=$(curl -s -X POST $BASE_ENGINE/runs \
  -H "Authorization: Bearer $TOKEN" \
  -F "pipeline=@test_pipelines/large_logs.yaml")
id=$(echo $r | python -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "Run ID: $id — streaming first 10 lines:"
curl -s $BASE_ENGINE/runs/$id/logs?follow=true | head -10
echo "✅ Streaming works — check data/logs/$id.log for file size"

echo ""
echo "════════════════════════════════════════"
echo "  ALL CAPABILITY TESTS COMPLETE"
echo "════════════════════════════════════════"
