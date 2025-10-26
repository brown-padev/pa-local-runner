import os
import sys
import json
import signal
import shutil
import pathlib
import secrets
import tempfile
import importlib
import argparse
import subprocess

from dataclasses import dataclass, field
#from stest import STest, SResults
#from summary import GSSummary

from enum import Enum

import yaml
import dacite

from result_types import ResultType

RESULTS_PREFIX = "auto_results"
WORK_PREFIX = pathlib.Path("/tmp/repo_run")

DEFAULT_REPO_PATH = WORK_PREFIX / "repos"
DEFAULT_WORK_PATH = WORK_PREFIX / "work"

OUTPUT_PREFIX = "auto_output"

def get_run_string():
    return secrets.token_bytes(4).hex()


class ExistMode(Enum):
    IGNORE = "ignore"
    PULL = "pull"
    RECLONE = "reclone"

    def __str__(self):
        return self.value


class RunSource(Enum):
    DISK = "disk"
    LIST = "list"
    GIT = "git"

class StepKind(Enum):
    PREPARE = "prepare"
    COMPILE = "compile"
    RUN = "run"


@dataclass
class ResultConfig():
    type: ResultType
    path: pathlib.Path = pathlib.Path("/dev/null")

@dataclass
class RunStep():
    name: str
    run: str
    kind: StepKind = StepKind.RUN
    results: ResultConfig = field(default_factory=ResultConfig)

@dataclass(frozen=False)
class RepoConfig():
    path: pathlib.Path = pathlib.Path(DEFAULT_REPO_PATH)
    list_file: pathlib.Path | None = None
    pattern: str = "*"
    github_org: str = ""
    time: str = ""
    source: RunSource = RunSource.DISK

@dataclass(frozen=False)
class RunConfig():
    repos: RepoConfig
    results_path: pathlib.Path
    summary_path: pathlib.Path
    steps: list[RunStep]
    run_id: str = ""

    @classmethod
    def make_default(cls, cwd: pathlib.Path):
        repos = RepoConfig(path=pathlib.Path(DEFAULT_REPO_PATH),
                           pattern="*",
                           github_org="",
                           source=RunSource.DISK)
        steps = []
        run_id = get_run_string()
        _results_path = cwd / RESULTS_PREFIX / "results_{}".format(run_id)

        return cls(repos=repos,
                   results_path=_results_path,
                   summary_path=(cwd / OUTPUT_PREFIX),
                   run_id=run_id,
                   steps=steps)

    @classmethod
    def make_or_load_args(cls, cwd: pathlib.Path, args):
        config = cls.make_default(cwd)
        if args.config:
            with open(args.config, "r") as in_fd:
                config_lines = in_fd.read()
                config_dict = yaml.safe_load(config_lines)
                config = dacite.from_dict(data_class=RunConfig,
                                          data=config_dict,
                                          config=dacite.Config(cast=[Enum, pathlib.Path]))

        config.update_config(args, cwd)
        return config

    def update_config(self, args, cwd: pathlib.Path):
        if args.run_id:
            self.run_id = args.run_id
        elif (not self.run_id):
            self.run_id = get_run_string()

        if args.repo_dir:
            self.repos.path = pathlib.Path(args.repo_dir)
        elif (not self.repos.path):
            self.repos.path = self.repos.path.resolve()

        def _res(attr, default):
            curr = getattr(self, attr)

            if not curr:
                setattr(self, attr, default)
            else:
                _res = curr.resolve()
                setattr(self, attr, _res)

        _res("results_path", cwd / RESULTS_PREFIX)
        _res("summary_path", cwd / OUTPUT_PREFIX)
        self.results_path = self.results_path / "results_{}".format(self.run_id)
