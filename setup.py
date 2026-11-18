from setuptools import setup

# All metadata is in setup.cfg (V21.3: PEP 517 build through pyproject.toml).
setup(
    entry_points={
        'console_scripts': [
            'wwb_scanner-ui = wwb_scanner.ui.launch:run',   # V21.3: clear message without Qt
        ],
    },
)
