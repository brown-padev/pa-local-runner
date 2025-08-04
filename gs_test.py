import os
import sys
import json
import pathlib
import argparse
import subprocess

HAS_PYSTACHE = False
try:
    import pystache
    HAS_PYSTACHE = True
except ImportError:
    print("Warning:  pystache not found, will not be able to generate summary info")

from dataclasses import dataclass

import colors as c
from colors import color, eprint

from collections import namedtuple, defaultdict

from stest import STest, SResults

STATUS_PASS = "passed"
STATUS_FAIL = "failed"

def _get(d, k, default=None):
    if k in d:
        return d[k]
    else:
        if default is not None:
            return default
        else:
            raise ValueError(f"{d} not in {d}")

class GSTest(STest):
    name: str
    output: str
    status: str
    score: float
    max_score: float
    output_format: str
    visibility: str
    tags: list[str]

    def __init__(self, name, output="", status="",
                 score=0.0, max_score=0.0,
                 output_format="text", visibility="visible",
                 tags=None):

        super(GSTest, self).__init__()
        self.name = name
        self.output = output
        self.status = status
        self.score = score
        self.max_score = max_score
        self.outut_format = output_format
        self.visibility = visibility

        if tags is None:
            self.tags = list()
        else:
            self.tags = tags

    def get_name(self):
        return self.name

    def get_output(self):
        return self.output

    def is_passing(self):
        if self.status != "":
            return self.status == STATUS_PASS
        else:
            return self.score == self.max_score

    def has_points(self):
        return self.max_score != 0

    def get_extra(self):
        return dict()

    def get_score_str(self):
        if self.has_points():
            return f"{self.score}/{self.max_score}"
        else:
            return ""

    def fmt_result(self):
        return c.color("PASS", c.OKGREEN) if self.is_passing() else c.color("FAIL", c.FAIL)

    @classmethod
    def from_json(cls, d):
        return GSTest(**d)


class GSResults(SResults):
    tests: list[GSTest]
    t_dict: dict[str, GSTest]
    execution_time: int
    t_passed: int
    t_failed: int
    t_total: int
    t_score: float
    t_max_score: float

    def __init__(self, execution_time, tests, **kwargs):
        self.execution_time = execution_time
        self.tests = tests
        self.t_dict = {t.name: t for t in tests}

        self._set_score_info()

    def get_tests(self):
        return self.tests

    def has_test(self, t_name):
        return t_name in self.t_dict

    def get_test(self, t_name) -> GSTest:
        if not self.has_test(t_name):
            raise ValueError("Result does not have test {}".format(t_name))
        else:
            return self.t_dict[t_name]

    def passed_test(self, t_name):
        test = self.get_test(t_name)
        return test.is_passing()

    def get_total_tests(self):
        return len(self.tests)

    def get_total_passed(self):
        return self.t_passed

    def get_total_failed(self):
        return self.t_failed

    def get_score(self):
        return self.t_score

    def get_max_score(self):
        return self.t_max_score

    def get_extra(self):
        return dict()

    @classmethod
    def from_json(cls, json_data):
        _tests = _get(json_data, "tests")
        _exec_time = _get(json_data, "execution_time", 0)
        tests = [GSTest.from_json(t) for t in _tests]

        return GSResults(_exec_time, tests)

    @classmethod
    def from_json_file(cls, json_file):
        with open(json_file, "r") as fd:
            json_data = json.load(fd)
            return cls.from_json(json_data)

    def show(self, descr_on_fail=True, descr_on_pass=True):
        total_points = 0.0
        points_earned = 0.0
        total = 0
        passed = 0
        failed = 0
        for test in self.tests:
            print("{}: {:10}  {}".format(test.fmt_result(), test.get_score_str(), test.name))
            show_output = (test.is_passing() and descr_on_pass) or ((not test.is_passing()) and descr_on_fail)
            if show_output:
                _output = test.output
                output = _output.replace("\n", "\n\t")
                print(f"\n{output}")

            if test.has_points():
                total_points += test.max_score
                points_earned += test.score

            if test.is_passing():
                passed += 1
            else:
                failed += 1

            total += 1

        print("\n**** SUMMARY ****")
        print("Tests:  {},  PASS:  {}, FAIL:  {}".format(total, passed, failed))
        print("Score:  {}/{}".format(points_earned, total_points))


    def _set_score_info(self):
        total_points = 0.0
        points_earned = 0.0
        total = 0
        passed = 0
        failed = 0

        for test in self.tests:
            if test.has_points():
                total_points += test.max_score
                points_earned += test.score

            if test.is_passing():
                passed += 1
            else:
                failed += 1

            total += 1

        self.t_passed = passed
        self.t_failed = failed
        self.t_total = total
        self.t_score = points_earned
        self.t_max_score = total_points
