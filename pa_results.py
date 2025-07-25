import os
import sys
import json
import signal
import shutil
import pathlib
import argparse
import tempfile
import subprocess

import dataclasses
from dataclasses import dataclass, field
from types import NoneType

from ctrf_results import CTRFTest, CTRFResults
import colors as c

STATUS_PASS = "passed"
STATUS_FAIL = "failed"


def _get(d, k, default=None, default_none=False):
    if k in d:
        return d[k]
    else:
        if (default is not None) or (default_none is True):
            return default
        else:
            raise ValueError(f"{d} not in {d}")


@dataclass(init=False)
class PaGradeEntry():
    title: str
    max: int
    hidden: bool = False
    no_total: bool = False
    max_visible: bool = False
    is_extra: bool = False
    concealed: bool = False

    def __init__(self, **kwargs):
        names = set([f.name for f in dataclasses.fields(self)])
        for k, v in kwargs.items():
            if k in names:
                 self.__setattr__(k, v)

    def to_json(self):
        d = {k: v for k, v in self.__dict__.items() if v is not False}
        return d

    @classmethod
    def from_json(cls, jd):
        try:
            ret = PaGradeEntry(**jd)
            return ret
        except Exception as e:
            import pdb; pdb.set_trace()
            raise e


@dataclass(init=False)
class PaRunner():
    title: str
    display_title: str
    command: str = "/bin/true"
    visible: bool = False
    transfer_warnings: bool = False
    xterm_js: bool = False
    require: str = None
    eval: str = None

    def __init__(self, **kwargs):
        names = set([f.name for f in dataclasses.fields(self)])
        for k, v in kwargs.items():
            if k in names:
                 self.__setattr__(k, v)

    def is_interactive(self):
        return self.xterm_js

    def to_json(self):
        return self.__dict__.copy()

    @classmethod
    def from_json(cls, jd):
        try:
            ret = PaRunner(**jd)
            return ret
        except Exception as e:
            import pdb; pdb.set_trace()
            raise e


@dataclass(init=True)
class PaConfig():
    key: str
    psetid: int
    title: str
    runners: dict[str,PaRunner]
    grades: dict[str,PaGradeEntry]
    directory: str = "."

    def __init__(self, **kwargs):
        names = set([f.name for f in dataclasses.fields(self)])
        for k, v in kwargs.items():
            if k in names:
                self.__setattr__(k, v)

    @classmethod
    def from_json_file(cls, config_file):
        def _decode(jd):
            if "runners" in jd:
                orig_runners = jd["runners"]
                jd["runners"] = {k: PaRunner.from_json(v) for k, v in orig_runners.items()}
            if "grades" in jd:
                orig_grades = jd["grades"]
                jd["grades"] = {k: PaGradeEntry.from_json(v) for k, v in orig_grades.items()}

            return jd

        with open(config_file, "r") as json_fd:
            _jd = json.load(json_fd, object_hook=_decode)
            try:
                ret = cls(**_jd)
                return ret
            except Exception as e:
                import pdb; pdb.set_trace()
                raise e


class PATestEntry(CTRFTest):
    name: str
    output: str
    status: str
    suite: str | None
    output_format: str
    visibility: str
    tags: list[str]
    duration: int

    def __init__(self, name="", output="", status="",
                 output_format="text", visibility="visible",
                 suite=None,
                 duration=0, tags=None):
        super(PATestEntry, self).__init__(name, status, suite=suite)
        self.output = output
        self.output_format = output_format
        self.visibility = visibility
        self.duration = duration

        if tags is None:
            self.tags = list()
        else:
            self.tags = tags

    def has_points(self):
        return False

    def get_score_str(self):
        return ""

    def fmt_result(self):
        return c.color("PASS", c.OKGREEN) if self.is_passing() else c.color("FAIL", c.FAIL)

    def to_json(self):
        return self.__dict__.copy()

    def build_ctrf_output(self, d):
        d["stdout"] = self.output.split("\n")

        _extra =  {
            "visibility": self.visibility,
            "output_format": self.output_format,
        }
        self.add_extra(_extra)

    @classmethod
    def from_basic_json(cls, d, suite=None):
        return PATestEntry(suite=suite, **d)

    @classmethod
    def add_from_ctrf(cls, d, kw):
        _output = _get(d, "stdout", "")
        kw["output"] = "\n".join(_output)

        cls._add_from_extra(kw, d, "visibility")
        cls._add_from_extra(kw, d, "output_format")


class PAResults(CTRFResults):
    tests: list[PATestEntry]
    t_dict: dict[str, PATestEntry]
    execution_time: int
    notes: dict | None
    grades: dict[str, PaGradeEntry] | None
    output: str
    runner_rv: int

    @dataclass
    class GradeSpec:
        name: str
        rubric: PaGradeEntry
        value: int | str | float

        def fmt_result(self):
            return "{} / {}".format(self.value, self.rubric.max)

    def __init__(self, execution_time=0, tests=None,
                 suite=None,
                 grades=None, notes=None,
                 **kwargs):
        super(PAResults, self).__init__(**kwargs)

        self.execution_time = execution_time
        self.tests = tests if tests is not None else []
        self.t_dict = {t.name: t for t in tests} if tests is not None else dict()

        self.suite = suite
        self.grades = grades
        self.notes = notes
        self.output = ""
        self.runner_rv = 0

        self._set_score_info()

    def get_tests(self) -> list[PATestEntry]:
        return self.tests

    def get_test_names(self):
        return list(self.t_dict.keys())

    def has_test(self, t_name):
        return t_name in self.t_dict

    def get_test(self, t_name, missing_ok=False) -> PATestEntry:
        if not self.has_test(t_name):
            if missing_ok:
                return None
            else:
                raise ValueError("Result does not have test {}".format(t_name))
        else:
            return self.t_dict[t_name]

    def passed_test(self, t_name):
        test = self.get_test(t_name)
        return test.is_passing()

    def has_tests(self):
        return len(self.tests) > 0

    def is_passing(self):
        if len(self.tests) == 0:
            # Want to trigger failure if no tests are present
            return False
        else:
            return all([t.is_passing() for t in self.tests])

    def add_grading_rubric(self, rubric_info: dict[str,PaGradeEntry]):
        self.grades = rubric_info

    def add_notes(self, notes: dict):
        self.notes = notes

    def add_notes_file(self, notes_file: pathlib.Path | str):
        with open(str(notes_file), "r") as fd:
            jd = json.load(fd)
            self.add_notes(jd)

    def has_notes(self):
        return self.notes is not None

    def get_grade_entry(self, name):
        assert(self.grades is not None)
        if name not in self.grades:
            raise ValueError("No grade entry found for {}".format(name))
        return self.grades[name]

    def get_note(self, name):
        assert(self.notes is not None)
        assert("autogrades" in self.notes)

        autogrades = self.notes["autogrades"]
        if name not in autogrades:
            raise ValueError("No grade note found for {}".format(name))
        return autogrades[name]

    def get_graded_items(self):
        if self.has_notes():
            assert(self.notes is not None)
            assert("autogrades" in self.notes)
            return list(self.notes["autogrades"].keys())
        else:
            return list()

    def get_graded_spec(self, name):
        entry = self.get_grade_entry(name)
        note = self.get_note(name)

        return self.GradeSpec(name, entry, note)

    def get_graded_notes(self):
        assert(self.grades is not None)
        assert(self.notes is not None)

        ret = [self.get_graded_spec(n) for n in self.get_graded_items()]
        return ret

    def add_run_output(self, output, rv):
        self.output = output
        self.runner_rv = rv

    @classmethod
    def from_empty(cls, suite=None):
        return PAResults(execution_time=0, tests=dict(), suite=suite)

    @classmethod
    def from_log(cls, log_file, rv, suite=None):
        results = PAResults(execution_time=0, tests=dict(), suite=suite)
        with open(log_file, "r") as log_fd:
            output = log_fd.read()
            results.add_run_output(output, rv)
        return results

    # @classmethod
    # def from_json(cls, json_data):
    #     _format = _get(json_data, "reportFormat")
    #     if _format != "CTRF":
    #         raise ValueError("YO")

    #     _tests = _get(json_data, "tests")
    #     _exec_time = _get(json_data, "execution_time", 0)
    #     _grades = _get(json_data, "grades", None, default_none=True)
    #     grades = {k: PaGradeEntry.from_json(v) for k, v in _grades.items()} \
    #         if _grades is not None else None
    #     notes = _get(json_data, "notes", None, default_none=True)
    #     tests = [PATestEntry.from_json(t) for t in _tests]

    #     return PAResults(_exec_time,
    #                      tests=tests,
    #                      grades=grades,
    #                      notes=notes)

    @classmethod
    def from_runner_json(cls, json_data, suite: str|None=None):
        _tests = _get(json_data, "tests")
        _exec_time = _get(json_data, "execution_time", 0)
        _grades = _get(json_data, "grades", None, default_none=True)
        grades = {k: PaGradeEntry.from_json(v) for k, v in _grades.items()} \
            if _grades is not None else None
        notes = _get(json_data, "notes", None, default_none=True)
        tests = [PATestEntry.from_basic_json(t, suite=suite) for t in _tests]

        return PAResults(_exec_time,
                         tests=tests,
                         suite=suite,
                         grades=grades,
                         notes=notes)
    @classmethod
    def from_runner_json_file(cls, json_file, suite=None):
        with open(json_file, "r") as fd:
            json_data = json.load(fd)
            return cls.from_runner_json(json_data, suite=suite)

    def build_ctrf_output(self, d):
        if self.grades is not None:
            _grades = {k: v.to_json() for k, v in self.grades.items()}
            self.add_extra_item("grades", _grades)

        if self.notes is not None:
            self.add_extra_item("notes", self.notes)

    @classmethod
    def add_from_ctrf(cls, d, kw):
        _tests = cls._get(d, "tests")
        tests = [PATestEntry.from_ctrf(t) for t in _tests]
        kw["tests"] = tests

        cls._add_from_extra(kw, d, "grades")
        cls._add_from_extra(kw, d, "notes")

    def write_json(self, out_file):
        with open(str(out_file), "w") as fd:
            json.dump(self.to_ctrf(), fd,
                      indent=2)

    def show_notes(self):
        if not self.has_notes():
            print("=== Grading notes ===")
            print("\tNo grading notes found")
            return

        print("=== Grading notes ===")
        for spec in self.get_graded_notes():
            print("  {}:  {}".format(spec.name, spec.fmt_result()))

    def show(self, print_tests=False, descr_on_fail=True, descr_on_pass=True):
        total = 0
        passed = 0
        failed = 0
        for test in self.tests:
            if print_tests:
                print("{}: {:10}  {}".format(test.fmt_result(), test.get_score_str(), test.name))
                show_output = (test.is_passing() and descr_on_pass) or ((not test.is_passing()) and descr_on_fail)
                if show_output:
                    _output = test.output
                    output = _output.replace("\n", "\n\t")
                    print(f"\n{output}")

            if test.is_passing():
                passed += 1
            else:
                failed += 1

            total += 1

        if len(self.tests) > 0:
            failed_str = "({} failed)".format(failed) if failed > 0 else ""
            print("=== Tests ===\n  Passed: {} / {} tests {} {:>31}".format(passed, total, failed_str, c.fmt_status_bool(failed == 0)))
        else:
            print("=== Tests ===\n" +
                  c.WARNING + "WARNING" + c.ENDC +
                  ":  No JSON tests results found.  JSON test results may not\n"
                  "be supported for all assignments, so this may or may not be an error.\n"
                  "If this assignment does not support JSON-based test output, please check\n"
                  "the output maunally (from the printed output, or log files)"
                  )

        self.show_notes()


    def _set_score_info(self):
        total = 0
        passed = 0
        failed = 0

        for test in self.tests:
            if test.is_passing():
                passed += 1
            else:
                failed += 1

            total += 1

        self.t_passed = passed
        self.t_failed = failed
        self.t_total = total

def main(input_args):
    t = PATestEntry("aaa", output="yo", status=STATUS_PASS)
    jd =t.to_ctrf()
    import pdb; pdb.set_trace()
    tt = PATestEntry.from_ctrf(jd)

    import pdb; pdb.set_trace()
    pass

if __name__ == "__main__":
    main(sys.argv[1:])
