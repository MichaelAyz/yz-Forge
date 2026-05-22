import os
import yaml
import httpx
import asyncio
import logging

logger = logging.getLogger("forge.alerts")

# Load config
try:
    CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.yaml")
    with open(CONFIG_PATH) as f:
        CONFIG = yaml.safe_load(f)
except Exception:
    CONFIG = {}

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL") or CONFIG.get("slack", {}).get("webhook_url", "")

async def _send_slack_payload(payload: dict):
    """Sends a payload to Slack webhook asynchronously without blocking."""
    if not SLACK_WEBHOOK_URL:
        logger.warning("Slack webhook URL is empty, skipping alert.")
        return
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(SLACK_WEBHOOK_URL, json=payload, timeout=5.0)
            if resp.status_code not in (200, 201, 204):
                logger.error(f"Slack webhook returned status code {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"Failed to send Slack alert asynchronously: {e}")

async def alert_pipeline_event(pipeline_name: str, run_id: str, status: str, duration_seconds: float = None, failing_job: str = None):
    """
    Sends an alert when a pipeline starts, succeeds, or fails.
    Required events: pipeline, run ID, duration, failing job if any.
    """
    color = "#36a64f" if status == "succeeded" else ("#fe2b2b" if status == "failed" else "#3aa3e3")
    emoji = "✅" if status == "succeeded" else ("❌" if status == "failed" else "🚀")
    
    text = f"{emoji} *Pipeline {status.capitalize()}*"
    
    fields = [
        {"title": "Pipeline", "value": pipeline_name, "short": True},
        {"title": "Run ID", "value": run_id, "short": True},
        {"title": "Status", "value": status.upper(), "short": True}
    ]
    
    if duration_seconds is not None:
        fields.append({"title": "Duration", "value": f"{duration_seconds:.2f}s", "short": True})
        
    if failing_job:
        fields.append({"title": "Failing Job", "value": failing_job, "short": False})
        
    payload = {
        "text": f"{emoji} Pipeline `{pipeline_name}` ({run_id}) is now *{status}*.",
        "attachments": [
            {
                "color": color,
                "title": f"Forge CI/CD Pipeline - {status.capitalize()}",
                "fields": fields,
                "ts": None
            }
        ]
    }
    await _send_slack_payload(payload)

async def alert_integrity_failure(run_id: str, name: str, version: str, expected_sha: str, actual_sha: str):
    """
    Sends an alert for an integrity failure.
    Must include tags for the right people being notified (e.g. <!channel>).
    """
    payload = {
        "text": "🚨 *<!channel> INTEGRITY FAILURE DETECTED!* An artifact package checksum mismatch occurred during pull time.",
        "attachments": [
            {
                "color": "#fe2b2b",
                "title": "Security & Integrity Alert",
                "fields": [
                    {"title": "Run ID", "value": run_id, "short": False},
                    {"title": "Artifact Coordinate", "value": f"{name}@{version}", "short": True},
                    {"title": "Expected SHA-256", "value": f"`{expected_sha}`", "short": False},
                    {"title": "Actual SHA-256", "value": f"`{actual_sha}`", "short": False}
                ]
            }
        ]
    }
    await _send_slack_payload(payload)

async def alert_resolution_failure(pipeline_name: str, details: str):
    """
    Sends an alert for a dependency resolution failure.
    Includes conflict or cycle details.
    """
    payload = {
        "text": f"⚠️ *Dependency Resolution Failure* for pipeline `{pipeline_name}`.",
        "attachments": [
            {
                "color": "#e0a106",
                "title": "Resolution/Dependency Error",
                "fields": [
                    {"title": "Pipeline", "value": pipeline_name, "short": True},
                    {"title": "Details", "value": details, "short": False}
                ]
            }
        ]
    }
    await _send_slack_payload(payload)
