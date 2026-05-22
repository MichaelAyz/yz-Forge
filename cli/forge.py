import os
import json
import click
import httpx
import hashlib
import yaml

CONFIG_PATH = os.path.expanduser("~/.forge/config")

def _save_config(config_data):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config_data, f)

def _load_config():
    if not os.path.exists(CONFIG_PATH):
        raise click.ClickException("Not logged in. Run 'forge login <url>' first.")
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def _get_headers():
    config = _load_config()
    return {"Authorization": f"Bearer {config['token']}"}

@click.group()
def cli():
    pass

@cli.command()
@click.argument("url")
def login(url):
    """Store credentials for a Forge server"""
    token = click.prompt("Token", hide_input=True)
    url = url.rstrip("/")
    
    try:
        # Just check health endpoint to ensure URL is valid
        with httpx.Client() as client:
            resp = client.get(f"{url}/health", timeout=5.0)
            if resp.status_code != 200:
                click.secho(f"Warning: Health check failed for {url}", fg="yellow")
    except httpx.RequestError:
        click.secho(f"Warning: Could not connect to {url}", fg="yellow")

    _save_config({"url": url, "token": token})
    click.secho(f"Logged in successfully to {url}", fg="green")

@cli.command()
@click.argument("pipeline")
def run(pipeline):
    """Submit a pipeline YAML"""
    if not os.path.exists(pipeline):
        raise click.ClickException(f"Pipeline file '{pipeline}' not found")
        
    config = _load_config()
    url = config["url"]
    headers = _get_headers()
    
    with open(pipeline, "rb") as f:
        files = {"pipeline": f}
        try:
            with httpx.Client() as client:
                resp = client.post(f"{url}/runs", headers=headers, files=files, timeout=30.0)
            if resp.status_code == 201:
                run_id = resp.json()["run_id"]
                click.echo(run_id)
            else:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                raise click.ClickException(f"Failed to submit pipeline ({resp.status_code}): {detail}")
        except httpx.RequestError as e:
            raise click.ClickException(f"Network error: {e}")

@cli.command()
@click.argument("run_id")
@click.option("--follow", is_flag=True)
def logs(run_id, follow):
    """Fetch logs for a run"""
    config = _load_config()
    url = config["url"]
    headers = _get_headers()
    
    follow_param = "true" if follow else "false"
    try:
        with httpx.Client() as client:
            with client.stream("GET", f"{url}/runs/{run_id}/logs", params={"follow": follow_param}, headers=headers, timeout=None) as resp:
                if resp.status_code != 200:
                    # Need to read the response if it failed before throwing error
                    resp.read()
                    raise click.ClickException(f"Failed to fetch logs ({resp.status_code}): {resp.text}")
                
                for line in resp.iter_lines():
                    if line:
                        if line.startswith("data: "):
                            log_content = line[6:]  # strip 'data: '
                            click.echo(log_content)
    except httpx.RequestError as e:
        raise click.ClickException(f"Network error: {e}")

@cli.command()
@click.argument("path")
@click.option("--name", required=True)
@click.option("--version", required=True)
def publish(path, name, version):
    """Publish an artifact"""
    if not os.path.exists(path):
        raise click.ClickException(f"File not found: {path}")
        
    config = _load_config()
    url = config["url"]
    headers = _get_headers()
    
    # Compute SHA-256
    sha256_hash = hashlib.sha256()
    with open(path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    checksum = f"sha256:{sha256_hash.hexdigest()}"
    
    with open(path, "rb") as f:
        files = {"file": (os.path.basename(path), f)}
        data = {"checksum": checksum}
        try:
            with httpx.Client() as client:
                resp = client.post(f"{url}/artifacts/{name}/{version}", headers=headers, files=files, data=data, timeout=60.0)
            if resp.status_code in (200, 201):
                click.secho(f"Artifact {name}@{version} published successfully!", fg="green")
            else:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                raise click.ClickException(f"Failed to publish artifact ({resp.status_code}): {detail}")
        except httpx.RequestError as e:
            raise click.ClickException(f"Network error: {e}")

@cli.command()
@click.argument("pipeline")
def resolve(pipeline):
    """Print lockfile without running"""
    if not os.path.exists(pipeline):
        raise click.ClickException(f"Pipeline file '{pipeline}' not found")
        
    with open(pipeline, "r") as f:
        try:
            data = yaml.safe_load(f)
        except Exception as e:
            raise click.ClickException(f"Failed to parse YAML: {e}")
            
    if not isinstance(data, dict):
        raise click.ClickException("Pipeline must be a YAML mapping")
        
    deps = data.get("dependencies", [])
    
    config = _load_config()
    url = config["url"]
    headers = _get_headers()
    
    try:
        with httpx.Client() as client:
            resp = client.post(f"{url}/resolve", headers=headers, json={"dependencies": deps}, timeout=30.0)
        if resp.status_code == 200:
            click.echo(json.dumps(resp.json(), indent=2))
        else:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise click.ClickException(f"Failed to resolve dependencies ({resp.status_code}): {detail}")
    except httpx.RequestError as e:
        raise click.ClickException(f"Network error: {e}")

@cli.command()
@click.argument("package")
def ls(package):
    """List versions of a package"""
    config = _load_config()
    url = config["url"]
    headers = _get_headers()
    
    try:
        with httpx.Client() as client:
            resp = client.get(f"{url}/artifacts/{package}", headers=headers, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            versions = data.get("versions", [])
            for version in versions:
                click.echo(version)
        else:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise click.ClickException(f"Failed to list versions ({resp.status_code}): {detail}")
    except httpx.RequestError as e:
        raise click.ClickException(f"Network error: {e}")

if __name__ == "__main__":
    cli()