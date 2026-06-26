#!/usr/bin/env python3
"""
Pull schemas from OMH, IEEE 1752

For each schema source:

1. clone the repo
2. stage a subdirectory into our data/schema directory
3. update a README with the exact versions, etc.

Currently loads:

1. IEEE 1752
2. OMH
3. IEEE 1752 1.1 metabolic DRAFT (1.1-metabolic branch) into the IEEE namespace
"""

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory

repo_root = Path(__file__).parents[1].resolve()
schema_root = repo_root / "data" / "schema"


@dataclass
class SchemaRepo:
    url: str
    dest: str
    subdir: str = ""
    ref: str = "HEAD"


# schema repos to pull from:
schema_repos = [
    SchemaRepo(
        dest="omh",
        url="https://github.com/openmhealth/schemas",
        subdir="schema/omh",
    ),
    SchemaRepo(
        dest="ieee",
        url="https://opensource.ieee.org/omh/1752.git",
        subdir="schemas",
    ),
    # metabolic draft: fetch into ieee
    # only the metabolic subdirectory
    SchemaRepo(
        dest="ieee/metabolic",
        url="https://opensource.ieee.org/omh/1752-2.git",
        ref="1.1-metabolic",
        subdir="schemas/metabolic",
    ),
]

readme_tpl = """
Schema source:

url: {url}
ref: `{resolved_ref}`
subdirectory: `{subdir}`

Do not edit files in this directory, update with `scripts/pull_schemas.py`
"""


def clone_schema(schema_repo: SchemaRepo):
    dest_path = schema_root / schema_repo.dest
    print(f"Cloning {schema_repo.url}@{schema_repo.ref}/{schema_repo.subdir} -> {dest_path}")
    if dest_path.exists():
        print(f"Clearing {dest_path}")
        shutil.rmtree(dest_path)
    with TemporaryDirectory() as td:
        base_path = Path(td)
        git_args = []
        if schema_repo.ref:
            git_args.extend(["--revision", schema_repo.ref])
        name = schema_repo.url.rpartition("/")[-1]
        run(
            [
                "git",
                "clone",
                "--depth=1",
                *git_args,
                schema_repo.url,
                name,
            ],
            check=True,
            cwd=td,
        )
        src_path = base_path / name
        p = run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=src_path,
        )
        resolved_ref = p.stdout.strip()

        if schema_repo.subdir:
            src_path = src_path / schema_repo.subdir

        print(f"Staging {src_path} -> {dest_path}")
        shutil.copytree(src_path, dest_path, symlinks=True)
        readme = readme_tpl.format(
            resolved_ref=resolved_ref,
            **asdict(schema_repo),
        )
        readme_path = dest_path / "README.md"
        print(f"Writing {readme_path}")
        with (readme_path).open("w") as f:
            f.write(readme)


def main():
    for schema_repo in schema_repos:
        clone_schema(schema_repo)


if __name__ == "__main__":
    main()
