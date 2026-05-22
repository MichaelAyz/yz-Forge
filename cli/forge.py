import os
import json
import click
import requests

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
    
    # We could optionally make a health check request here to verify the url/token
    try:
        # Just check health endpoint to ensure URL is valid
        resp = requests.get(f"{url}/health", timeout=5)
        if resp.status_code != 200:
            click.secho(f"Warning: Health check failed for {url}", fg="yellow")
    except requests.RequestException:
        click.secho(f"Warning: Could not connect to {url}", fg="yellow")

    _save_config({"url": url, "token": token})
    click.secho(f"Logged in successfully to {url}", fg="green")

@cli.command()
@click.argument("pipeline")
def run(pipeline):
    """Submit a pipeline YAML"""
    raise NotImplementedError

@cli.command()
@click.argument("run_id")
@click.option("--follow", is_flag=True)
def logs(run_id, follow):
    """Fetch logs for a run"""
    raise NotImplementedError

@cli.command()
@click.argument("path")
@click.option("--name", required=True)
@click.option("--version", required=True)
def publish(path, name, version):
    """Publish an artifact"""
    raise NotImplementedError

@cli.command()
@click.argument("pipeline")
def resolve(pipeline):
    """Print lockfile without running"""
    raise NotImplementedError

@cli.command()
@click.argument("package")
def ls(package):
    """List versions of a package"""
    raise NotImplementedError

if __name__ == "__main__":
    cli()