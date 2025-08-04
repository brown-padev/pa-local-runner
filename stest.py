

STATUS_PASS = "passed"
STATUS_FAIL = "failed"

class STest():

    def __init__(self):
        pass

    def get_name(self):
        raise NotImplementedError("Subclass must implement")

    def has_points(self):
        return False

    def get_score_str(self):
        raise NotImplementedError("Subclass must implement")

    def is_passing(self):
        raise NotImplementedError("Subclass must implement")

    def get_extra(self):
        raise NotImplementedError("Subclass must implement")

    def get_output(self):
        raise NotImplementedError("Subclass must implement")

    def fmt_result(self):
        raise NotImplementedError("Subclass must implement")

class SResults():

    def __init__(self):
        pass

    def get_tests(self):
        raise NotImplementedError("Subclass must implement")

    def get_total_tests(self):
        raise NotImplementedError("Subclass must implement")

    def get_total_passed(self):
        raise NotImplementedError("Subclass must implement")

    def get_total_failed(self):
        raise NotImplementedError("Subclass must implement")

    def get_score(self):
        raise NotImplementedError("Subclass must implement")

    def get_max_score(self):
        raise NotImplementedError("Subclass must implement")

    def get_extra(self):
        raise NotImplementedError("Subclass must implement")

    def show(self, descr_on_fail=True, descr_on_pass=True):
        total_points = 0.0
        points_earned = 0.0
        total = 0
        passed = 0
        failed = 0
        for test in self.get_tests():
            print("{}: {:10}  {}".format(test.fmt_result(), test.get_score_str(), test.get_name()))
            show_output = (test.is_passing() and descr_on_pass) or ((not test.is_passing()) and descr_on_fail)
            if show_output:
                _output = test.get_output()
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
