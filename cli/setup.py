from setuptools import setup, find_packages

setup(
    name="forge-cli",
    version="0.1.0",
    py_modules=["forge"],
    install_requires=[
        "click",
        "requests",
        "httpx"
    ],
    entry_points={
        "console_scripts": [
            "forge=forge:cli",
        ],
    },
)
