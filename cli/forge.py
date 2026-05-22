import click

# TODO Person 3

@click.group()
def cli():
    pass

@cli.command()
@click.argument("url")
def login(url):
    """Store credentials for a Forge server"""
    raise NotImplementedError

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