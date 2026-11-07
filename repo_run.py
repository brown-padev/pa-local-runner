#!/usr/bin/env python3

import os
import sys
import json
import time
import signal
import shutil
import pathlib
import secrets
import tempfile
import importlib
import argparse
import threading
import subprocess

from dataclasses import dataclass, field
from stest import STest, SResults
from summary import GSSummary

from enum import Enum

import yaml
import dacite

import colors as c

STATUS_PASS = "passed"
STATUS_FAIL = "failed"


VERBOSE_MODE = True
SCRIPT_PATH = pathlib.Path(os.path.dirname(os.path.realpath(__file__)))
RUNNING_PGID = None
ABORT = False
INTERRUPTED = False

# How long to wait for a process group to die after SIGTERM before SIGKILLing it.
KILL_GRACE_SEC = 2.0

from result_types import ResultLoader, ResultType

import config as cfg
from config import ExistMode, RunSource, RunConfig, \
    RepoConfig, RunStep


def _kill_group(pgid, leader=None, grace=KILL_GRACE_SEC):
    """Terminate every process in `pgid`:  SIGTERM, then SIGKILL if needed.

    `leader` is the Popen for the group leader, if we have it;  it gets reaped
    between the two signals (see below).  Never raises -- a group that is
    already gone is the common case, not an error.
    """
    if pgid is None:
        return

    # Safety net.  If we ever compute a PGID wrong, we must not take down the
    # grader (and the operator's shell) along with the student's processes.
    if pgid == os.getpgrp():
        print(f"Refusing to kill own process group (PGID {pgid})")
        return

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return                      # nothing left in the group

        # Reap the leader before probing:  an unreaped zombie still counts as a
        # member of the group, so killpg(pgid, 0) below would never report the
        # group as gone and we would always burn the full grace period.
        if leader is not None:
            try:
                leader.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass

        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)      # probe:  does anyone still exist?
            except ProcessLookupError:
                return
            time.sleep(0.05)


def _reclaim_tty():
    """Make our own process group the terminal's foreground group again.

    After an attached child exits, the terminal still points at the (now dead)
    child's group.  Without this, the next read from the tty -- e.g. the
    interrupt prompt in main() -- would stop us with SIGTTIN.
    """
    try:
        tty = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return                          # no controlling terminal
    try:
        # We are a background group at this point, so tcsetpgrp would SIGTTOU us.
        hdlr = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(tty, os.getpgrp())
        signal.signal(signal.SIGTTOU, hdlr)
    except OSError:
        pass
    finally:
        os.close(tty)


def _sigint_handler(sig, frame):
    global RUNNING_PGID
    global ABORT

    # This only fires for a SIGINT delivered while *we* are the foreground group
    # -- i.e. between students, during clone/copy/summary.  A Ctrl-C during an
    # attached student step goes to the child's group instead and is detected
    # from its exit status in RepoRunner.do_run().
    if RUNNING_PGID is not None:
        print(f"Ending currently running test (PGID {RUNNING_PGID})...")
        _kill_group(RUNNING_PGID)

    # There is no current student to skip here, so stop the whole run.
    ABORT = True


def do_exec(cmd, check=True, shell=True, cwd=None, return_rv=False):
    global VERBOSE_MODE

    if VERBOSE_MODE:
        print("Executing:  {}".format(" ".join(cmd) if isinstance(cmd, list) else cmd))

    proc = subprocess.run(" ".join(cmd) if shell and isinstance(cmd, list) else cmd, shell=shell, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)

    if check and proc.returncode != 0:
        do_exit(f"Command exited with {proc.returncode}:  {proc.stdout}")

    output = proc.stdout
    if return_rv:
        return output, proc.returncode
    else:
        return output

def _drain(pipe, log_fd):
    """Copy a child's output to both our stdout and the step log, until EOF."""
    for line in pipe:
        sys.stdout.buffer.write(line)
        log_fd.write(line)


def do_exec_live(cmd, log_file=None, shell=True, check=False, attach=False, cwd=None):
    global VERBOSE_MODE
    global RUNNING_PGID

    if VERBOSE_MODE:
        print("Executing:  {}".format(" ".join(cmd) if isinstance(cmd, list) else cmd))

    def _become_tty_fg():
        # The child is already the leader of its own process group here:
        # process_group=0 below is applied by CPython before preexec_fn runs.
        hdlr = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        tty = os.open("/dev/tty", os.O_RDWR)
        try:
            os.tcsetpgrp(tty, os.getpgrp())
        finally:
            os.close(tty)
        signal.signal(signal.SIGTTOU, hdlr)

    if (log_file is None) and (not attach):
        raise ValueError("Log file must be defined for non-interactive process")

    log_fd = open(str(log_file), "wb") if (not attach) else None

    cmd_to_run = " ".join(cmd) if shell and isinstance(cmd, list) else cmd
    kwargs = {}
    if attach:
        kwargs["preexec_fn"] = _become_tty_fg
    else:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
        kwargs["bufsize"] = 128

    # process_group=0 puts the child in a brand new process group whose PGID
    # equals its PID, so we know the PGID without asking the kernel.  Calling
    # os.getpgid(proc.pid) here would race the child's setpgid() and could
    # hand back *our* PGID, pointing the kill below at the grader itself.
    proc = subprocess.Popen(cmd_to_run, shell=shell, cwd=cwd,
                            process_group=0, **kwargs)
    pgid = proc.pid
    RUNNING_PGID = pgid

    timed_out = False
    reader = None

    try:
        if not attach:
            # Drain off the critical path, so that the timeout below actually
            # applies while the child is still producing output.
            reader = threading.Thread(target=_drain,
                                      args=(proc.stdout, log_fd), daemon=True)
            reader.start()

        try:
            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(pgid, leader=proc)
    finally:
        # Runs on the normal path too:  the shell can exit successfully while
        # leaving background children alive in the group.
        _kill_group(pgid, leader=proc)

        if reader is not None:
            # The pipe only reaches EOF once every writer in the group is gone,
            # which the kill above guarantees.
            reader.join(timeout=5)

        if attach:
            _reclaim_tty()

        if log_fd is not None:
            if timed_out:
                msg = b"[Timed out]\n"
                log_fd.write(msg)
                sys.stdout.buffer.write(msg)
            log_fd.flush()
            log_fd.close()

        RUNNING_PGID = None

    if check and proc.returncode != 0:
        do_exit(f"Command exited with {proc.returncode}:  {proc.stdout}")

    return proc

def check_bin_exists(bin_name):
    bin_path = pathlib.Path(bin_name)

    if not bin_path.exists():
        print(f"Could not find binary:  {bin_path}, exiting")
        sys.exit(1)


def load_json(input_file):
    with open(input_file, "r") as fd:
        json_data = json.load(fd)
        return json_data


def write_json(d, target_file):
    with open(target_file, "w") as fd:
        json.dump(d, fd, indent=True, sort_keys=True)


def do_exit(message):
    print(message)
    sys.exit(1)

def mkdir(dir_name):
    pathlib.Path.mkdir(pathlib.Path(dir_name),
                       exist_ok=True, parents=True)


class RepoUrl():
    url: str
    org: str
    name: str

    def __init__(self, url):
        self.url = url
        self.org, self.name = self._parse(url)

    def _parse(self, url: str):
        if url.startswith("http"):
            bare = url.replace("https://github.com/", "")
            tokens = bare.split("/")
            if len(tokens) != 2:
                import pdb; pdb.set_trace()
                pass
            org, name = tokens[0], tokens[1]

        elif url.startswith("git@"):
            bare = url.replace("git@github.com:", "")
            bare = bare.replace(".git", "")
            tokens = bare.split("/")
            if len(tokens) != 2:
                import pdb; pdb.set_trace()
                pass
            org, name = tokens[0], tokens[1]
        else:
            raise ValueError("Unrecognized URL type :  {}".format(url))

        return org, name

    def get_ssh(self):
        return f"git@github.com:{self.org}/{self.name}.git"

    def get_https(self):
        return f"https://github.com/{self.org}/{self.name}"

    def stem(self):
        return self.name

class Repo():

    def __init__(self):
        pass

    def name(self):
        raise NotImplementedError("Subclass must implement")

    def setup(self, target_dir: pathlib.Path):
        raise NotImplementedError("Subclass must implement")

class DiskRepo(Repo):
    path: pathlib.Path

    def __init__(self, path):
        super(DiskRepo, self).__init__()
        self.path = pathlib.Path(path)

    def name(self):
        return self.path.stem

    def setup(self, target_dir: pathlib.Path, config: RunConfig|None=None):
        do_exec(f"cp -pTRv {str(self.path)} {str(target_dir)}")

class GitRepo(Repo):
    url: RepoUrl

    def __init__(self, url: str | RepoUrl):
        super(GitRepo, self).__init__()
        if isinstance(url, RepoUrl):
            self.url = url
        else:
            self.url = RepoUrl(url)

    def name(self):
        return self.url.name

    def setup(self, target_dir: pathlib.Path, config: RunConfig|None=None):
        output, rv = do_exec(f"git clone {str(self.url.get_ssh())} {str(target_dir)}", check=False, return_rv=True)
        tries = 0
        while rv != 0 and tries < 10:
            print("Clone exited with rv {}, retrying;  output was {}".format(rv, output))
            time.sleep(1)
            output, rv = do_exec(f"git clone {str(self.url.get_ssh())} {str(target_dir)}", check=False, return_rv=True)
            tries += 1

        if config and config.repos.time:
            t = config.repos.time
            cwd = str(target_dir)
            current_branch = do_exec("git rev-parse --abbrev-ref HEAD", cwd=cwd)
            last_commit = do_exec(f"git rev-list -n 1 --before=\"{t}\" \"{current_branch}\"", cwd=cwd)
            if not last_commit:
                print("Warning:  no valid commit found before due date, getting latest commit instead")
                return

            do_exec(f"git tag submit {last_commit}", cwd=cwd)
            do_exec(f"git checkout submit", cwd=cwd)

class RepoList():
    repo_path: pathlib.Path
    mode: RunSource
    config: RepoConfig
    repos: list[Repo]

    def __init__(self, config: RepoConfig):
        self.config = config
        self.mode = config.source
        self.repo_path = pathlib.Path(config.path)

        self.repos = []
        self._build_list()

    def _build_list(self):
        if self.mode == RunSource.DISK:
            self.repos = self._get_disk()
        elif self.mode == RunSource.LIST:
            self.repos = self._get_list()
        else:
            raise NotImplementedError("TODO")

    def _get_list(self):
        assert(self.config.list_file is not None)
        list_file = self.config.list_file
        with open(list_file, "r") as fd:
            _urls = fd.readlines()
            urls = [GitRepo(r.strip()) for r in _urls if r.strip()]

        return urls

    def _get_disk(self):
        assert(self.repo_path is not None)
        if self.repo_path == cfg.DEFAULT_REPO_PATH:
            mkdir(self.repo_path)

        if not self.repo_path.exists():
            raise ValueError("Repo path {} does not exist".format(str(self.repo_path)))

        urls = [DiskRepo(x) for x in self.repo_path.glob(self.config.pattern)]
        return urls

    def get_repos(self):
        return self.repos

class RepoRunner():

    def __init__(self, config: RunConfig):
        self.config = config
        self.work_path = cfg.DEFAULT_WORK_PATH
        self.results_path = config.results_path
        self.run_id = config.run_id

    def w_path(self, ext=None):
        if ext is not None:
            return str(self.repo_path / ext)
        else:
            return str(self.repo_path)

    def w_fp(self, url):
        name = f"{url.name}"
        return self.repo_path / name

    def r_path(self, ext=None):
        if ext is not None:
            return str(self.results_path / ext)
        else:
            return str(self.results_path)

    def r_fp(self, url):
        name = f"{url.name}.json"
        return self.results_path / name

    def do_run(self, repo: Repo):
        global INTERRUPTED

        def _msg(s):
            print(c.OKCYAN + "[repo_run, {}] {}".format(repo.name(),
                                                        s) + c.ENDC)

        work_dir = self.work_path
        final_results_file = self.results_path / "{}.json".format(repo.name())

        if work_dir.exists():
            shutil.rmtree(work_dir)
        mkdir(work_dir)
        repo.setup(work_dir)


        def _parse_cmd(cmd: str):
            cmd = cmd.replace("${{ repo }}", str(work_dir))
            cmd = cmd.replace("${{ repo_name }}", repo.name())
            return cmd

        runner_results_file = None
        result_type = ResultType.NONE

        for step in self.config.steps:
            cmd = step.run
            _msg("Running step {}".format(step.name))
            with tempfile.NamedTemporaryFile() as log_fd:
                _cmd = _parse_cmd(cmd)
                proc = do_exec_live(_cmd, log_fd.name, shell=True,
                                    cwd=str(work_dir), attach=True)
                _msg("Step {} exited with return value {}".format(step.name, proc.returncode))

            # Ctrl-C goes to the child's process group, not to us, so we learn
            # about it from the exit status.  Bash reports -SIGINT for simple
            # commands, "a; b" sequences and pipelines alike.
            if proc.returncode == -signal.SIGINT:
                INTERRUPTED = True
                _msg("Interrupted by user, skipping remaining steps")
                break

            if step.results.type != ResultType.NONE:
                runner_results_file = step.results.path
                result_type = step.results.type

        if INTERRUPTED:
            # No results to collect;  main() decides whether to go on.
            return ResultType.NONE, final_results_file

        if result_type != ResultType.NONE:
            if runner_results_file is None:
                raise ValueError("No result file generated after running steps")

            assert(runner_results_file is not None)
            if not runner_results_file.exists():
                raise ValueError(f"No results found at {str(runner_results_file)}")

            do_exec(f"cp -v {str(runner_results_file)} {str(final_results_file)}")

        return result_type, final_results_file



def main(input_args):
    global RUNNING_PGID
    global ABORT
    global INTERRUPTED

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str)
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--repo-dir", type=str)

    commands = [
         "fetch", "run", "summarize", "build", "all",
    ]

    subparsers = parser.add_subparsers(dest="command")
    sp_cmds = {c: subparsers.add_parser(c) for c in commands}

    for c in ["fetch", "run", "build"]:
        sp_cmds[c].add_argument("--url", type=str)
        sp_cmds[c].add_argument("--repo-list", type=str)
    sp_cmds["fetch"].add_argument("--exist-mode", type=ExistMode,
                                  default=ExistMode.IGNORE,
                                  choices=list(ExistMode))
    sp_cmds["summarize"].add_argument("results_dir", nargs="*")

    args = parser.parse_args(input_args)
    cwd = pathlib.Path(os.getcwd())

    config = RunConfig.make_or_load_args(cwd, args)

    run_id = config.run_id
    print("Using run ID {}".format(run_id))

    repo_list = RepoList(config.repos)

    runner = RepoRunner(config)

    needs_fetch = args.command == "fetch" or args.command == "all"
    needs_build = args.command == "build" or args.command == "all"
    needs_run = args.command == "run" or args.command == "all"
    needs_summary = args.command == "summarize" or needs_run

    if needs_fetch or needs_build or needs_run:
        mkdir(config.repos.path)
        mkdir(config.results_path)

    if needs_summary:
        mkdir(config.summary_path)


    urls = []

    all_repos = repo_list.get_repos()
    summary = GSSummary(run_id)

    for i, repo in enumerate(all_repos):
        if needs_run or needs_build:
            signal.signal(signal.SIGINT, _sigint_handler)
            try:
                result_type, results_file = runner.do_run(repo)
            finally:
                signal.signal(signal.SIGINT, signal.SIG_DFL)

            if INTERRUPTED:
                INTERRUPTED = False
                if not sys.stdin.isatty():
                    # Unattended run:  an interrupt means stop, not skip.
                    ABORT = True
                elif i < len(all_repos) - 1:
                    # We hold the terminal again (see _reclaim_tty), so a
                    # Ctrl-D here is a real EOF on this read.
                    try:
                        ans = input("  [Enter] next student   "
                                    "[q or Ctrl-D] abort run: ")
                        if ans.strip().lower() == "q":
                            ABORT = True
                    except EOFError:
                        print()
                        ABORT = True

                if ABORT:
                    break
                continue

            if ABORT:
                break

            if (result_type != ResultType.NONE) and (results_file.exists()):
                print("Found results at {}".format(results_file))
                results = ResultLoader.load_results(result_type, results_file)
                results.show(descr_on_fail=True, descr_on_pass=False)
                summary.add(repo.name(), results)

            if result_type == ResultType.NONE:
                print("Runner did not produce results, not generating summary")
                needs_summary = False

    if needs_summary:
        search_path = config.results_path
        # if args.results_dir:
        #     if len(args.results_dir) == 1:
        #         search_path = pathlib.Path(args.results_dir).resolve()
        #     elif len(args.results_dir) > 1:
        #         raise NotImplementedError("TODO")

        if not needs_run:
            for fp in search_path.glob("*.json"):
                results = ResultLoader.load_results(ResultType.AUTO, fp)
                name = fp.stem
                summary.add(name, results)

        if not summary.result_map:
            # Nothing to report -- e.g. the run was aborted before any repo
            # produced results.  do_summary() divides by the submission count.
            print("No results collected, not generating summary")
            needs_summary = False

    if needs_summary:
        output_file = config.summary_path / "summary_{}.html".format(run_id)
        summary.do_summary(str(output_file))
        print("Summary written to {}".format(str(output_file)))

    print("Completed run {}".format(run_id))



if __name__ == "__main__":
   main(sys.argv[1:])
