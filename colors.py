HEADER = "\033[95m"
OKBLUE = "\033[94m"
OKCYAN = "\033[96m"
OKGREEN = "\033[92m"
WARNING = "\033[93m"
FAIL = "\033[91m"
ENDC = "\033[0m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"


def color(s, color):
    """Color a string via escape codes"""
    return color + s + ENDC


def eprint(s):
    print(color("Error: ", FAIL) + s)


def print_mismatch(name, got, expected):
    """Print a mismatch message for test paramater `name`"""
    print(color(name, BOLD) + " mismatch:")
    print("\t" + color("Got: ", FAIL) + str(got))
    print("\t" + color("Expected: ", OKGREEN) + str(expected))

def fmt_status_bool(s:  bool, pass_str="PASS", fail_str="FAIL"):
    c = OKGREEN if s else FAIL
    s = pass_str if s else fail_str
    return "[{}{}{}]".format(c, s, ENDC)
