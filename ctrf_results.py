import os
import sys
import json
import signal
import shutil
import pathlib
import argparse
import tempfile
import functools
import subprocess

import dataclasses
from dataclasses import dataclass, field
from types import NoneType

from stest import STest, SResults

import colors as c

from enum import Enum

def _get(d, k, default=None, default_none=False):
    if k in d:
        return d[k]
    else:
        if (default is not None) or (default_none is True):
            return default
        else:
            raise ValueError(f"{d} not in {d}")


STATUS_PASS = "passed"
STATUS_FAIL = "failed"

class TestStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    PENDING = "pending"
    OTHER = "other"

class CTRFTest(STest):
    name: str
    status: str
    duration: int = 0
    suite: str | None = None
    extra: dict

    def __init__(self,
                 name: str,
                 status: str,
                 duration: int=0,
                 suite: str|None = None,
                 **kwargs):

        super(STest, self).__init__()
        self.name = name
        self.status = status
        self.duration = duration
        self.suite = suite
        self.tags = []
        self.extra = dict()
        self.extra.update(kwargs)

    def get_name(self):
        return self.name

    def is_passing(self):
        return self.status == STATUS_PASS

    def fmt_result(self):
        return c.color("PASS", c.OKGREEN) if self.is_passing() else c.color("FAIL", c.FAIL)

    def add_extra(self, d: dict):
        self.extra.update(d)

    def add_extra_item(self, k, v):
        self.extra[k] = v

    def get_extra(self):
        return self.extra

    def fmt_result(self):
        return c.color("PASS", c.OKGREEN) if self.is_passing() else c.color("FAIL", c.FAIL)

    def get_score_str(self):
        return ""

    def build_ctrf_output(self, d):
        # Subclass will override
        raise NotImplementedError("yo")

    def get_output(self):
        d = dict()
        self.build_ctrf_output(d)
        output = "\n".join(d["stdout"])
        return output

    def to_ctrf(self):
        d = {
            "name": self.name,
            "status": self.status,
            "duration": 0,
            "tags": self.tags,
            "extra": self.extra,
        }
        if self.suite:
            d["suite"] = self.suite

        self.build_ctrf_output(d)
        return d

    @classmethod
    def _get(cls, d, k):
        if k in d:
            return d[k]
        else:
            raise ValueError(f"{d} not in {d}")

    @classmethod
    def _get_maybe(cls, d, k, default):
        if k in d:
            return d[k]
        else:
            return default


    @classmethod
    def _addif(cls, kw, d, k, k_arg=None):
        if k in d:
            kw[k_arg if k_arg else k] = d[k]

    @classmethod
    def _add_from_extra(cls, kw, d, k, k_arg=None):
        if "extra" in d and k in d["extra"]:
            kw[k_arg if k_arg else k] = d["extra"][k]

    @classmethod
    def add_from_ctrf(cls, d, kw):
        # Subclass may implement
        raise NotImplementedError("yo")
        pass

    @classmethod
    def from_ctrf(cls, d):
        kwargs = {}

        cls._addif(kwargs, d, "name")
        cls._addif(kwargs, d, "status")
        cls._addif(kwargs, d, "duration")

        if "suite" in d:
            kwargs["suite"] = d["suite"]

        cls.add_from_ctrf(d, kwargs)

        return cls(**kwargs)

@dataclass
class CTRFTool():
    name: str = "pa_run"
    version: str = "0.1"
    extra: dict = field(default_factory=dict)

    def to_json(self):
        return self.__dict__.copy()

    @classmethod
    def from_json(cls, d):
        return CTRFTool(**d)


class CTRFResults(SResults):
    report_format: str
    version: str
    tool: CTRFTool
    extra: dict

    def __init__(self, report_format="CTRF", version="0.0.0",
                 tool: CTRFTool|None = None):
        super(CTRFResults, self).__init__()
        self.report_format = report_format
        self.version = version
        self.tool = CTRFTool() if tool is None else tool
        self.extra = dict()

    def get_tests(self):
        raise NotImplementedError("Subclass must implement")

    def get_total_tests(self):
        summary = self._make_summary()
        return summary["tests"]

    def get_total_passed(self):
        summary = self._make_summary()
        return summary["passed"]

    def get_total_failed(self):
        summary = self._make_summary()
        return summary["failed"]

    def get_score(self):
        return 0.0

    def get_max_score(self):
        return 0.0

    def get_extra(self):
        return self.extra

    @classmethod
    def _addif(cls, kw, d, k, k_arg=None):
        if k in d:
            kw[k_arg if k_arg else k] = d[k]

    @classmethod
    def _add_from_extra(cls, kw, d, k):
        if "extra" in d and k in d["extra"]:
            kw[k] = d["extra"][k]

    @classmethod
    def _get(cls, d, k):
        if k in d:
            return d[k]
        else:
            raise ValueError(f"{d} not in {d}")

    def add_extra(self, d: dict):
        self.extra.update(d)

    def add_extra_item(self, k, v):
        self.extra[k] = v

    @functools.cache
    def _make_summary(self):
        total = 0
        passed = 0
        failed = 0

        for t in self.get_tests():
            if t.is_passing():
                passed += 1
            else:
                failed += 1

            total += 1

        ret = {
            "tests": total,
            "passed": passed,
            "failed": failed,
            "pending": 0,
            "skipped": 0,
            "other": 0,
            "suites": 1,
            "start": 0,
            "end": 0,
        }

        return ret

    def _make_results(self):
        ret = {
            "tool": self.tool.to_json(),
            "tests": [t.to_ctrf() for t in self.get_tests()],
            "summary": self._make_summary(),
        }
        return ret

    def build_ctrf_output(self, d):
        # Subclass will override
        raise NotImplementedError("yo")
        pass

    @classmethod
    def add_from_ctrf(cls, d, kw):
        # Subclass may implement
        raise NotImplementedError("yo")
        pass

    def to_ctrf(self):
        ret = {
            "reportFormat": self.report_format,
            "version": self.version,
            "results": self._make_results()
        }

        self.build_ctrf_output(ret)

        return ret


    @classmethod
    def from_ctrf(cls, d):
        kwargs = {}

        cls._addif(kwargs, d, "reportFormat", k_arg="report_format")
        cls._addif(kwargs, d, "version")

        tool = CTRFTool.from_json(d["tool"]) if "tool" in d else CTRFTool()
        kwargs["tool"] = tool

        cls.add_from_ctrf(d, kwargs)

        return cls(**kwargs)

    @classmethod
    def from_json_file(cls, json_file):
        with open(json_file, "r") as fd:
            json_data = json.load(fd)
            return cls.from_ctrf(json_data)


class Test_PATestEntry(CTRFTest):
    output: str
    output_format: str
    visibility: str
    tags: list[str]
    duration: int

    def __init__(self, name, output="", status="",
                 output_format="text", visibility="visible",
                 duration=0, tags=None):
        super(Test_PATestEntry, self).__init__(name, status)

        self.output = output
        self.output_format = output_format
        self.visibility = visibility
        self.duration = duration

        if tags is None:
            self.tags = list()
        else:
            self.tags = tags

    def as_ctrf(self, d):
        pass


    @classmethod
    def from_basic_json(cls, d):
        return PATestEntry(**d)

