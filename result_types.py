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


class ResultType(Enum):
    NONE = "none"
    AUTO = "auto"
    GRADESCOPE = "gradescope"
    PA = "pa"

SUPPORTED_RESULT_TYPES = set()

RESULT_MODULES = [
    (ResultType.GRADESCOPE, "gs_test"),
    (ResultType.PA, "pa_results"),
]

for rmod in RESULT_MODULES:
    rtype, mod_name = rmod
    try:
        globals()[mod_name] = importlib.import_module(mod_name)
        SUPPORTED_RESULT_TYPES.add(rtype)
    except ImportError:
        pass


class ResultLoader:

    @classmethod
    def load_results(cls, result_type, result_file):
        _type = result_type

        if result_type == ResultType.AUTO:
            with open(result_file, "r") as json_fd:
                jd = json.load(json_fd)
                if "reportFormat" in jd and jd["reportFormat"] == "CTRF":
                    _type = ResultType.PA
                if ("autograder_output" in jd) or ("stdout_visibility" in jd):
                    _type = ResultType.GRADESCOPE

        if _type == ResultType.AUTO:
            raise ValueError("Could not auto-detect JSON results")

        if _type == ResultType.GRADESCOPE:
            assert(ResultType.GRADESCOPE in SUPPORTED_RESULT_TYPES)

            res = gs_test.GSResults.from_json_file(result_file)
            return res
        elif _type == ResultType.PA:
            assert(ResultType.PA in SUPPORTED_RESULT_TYPES)
            res = pa_results.PAResults.from_json_file(result_file)
            return res
        else:
            raise NotImplementedError(f"Unsupported result type {result_type}")
