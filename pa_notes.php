<?php

class FakePset {
    public $grades = array();
}

class FakeResult {
    function __construct() {
        // Do nothing
    }

    function fetch_row() {
        return array();
    }
}

class FakeConf {
    function __construct() {
        // Do nothing
    }

    function qe($arg) {
        return new FakeResult();
    }
}

class FakePsetInfo {

    public $_log_file;
    public $_notes;
    public $pset;
    public $conf;

    function __construct($log_file, $pset_json) {
        $this->_log_file = $log_file;
        $this->_notes = null;
        $this->conf = new FakeConf();

        if ($pset_json) {
            $this->load_pset($pset_json);
        } else {
            $this->pset = new FakePset();
        }
    }

    private function load_pset($json_file) {
        $c = file_get_contents($json_file);
        $jd = json_decode($c, true);
        //var_dump($jd);

        // Turn the JSON dictionary into an object to allow access like a class
        // this works only if the names in the JSON line up with the actual PA Pset class
        $this->pset = (object)$jd;

        // Convert each nested GradeEntry into an object
        $_grades = $this->pset->grades;
        $grades = array();
        foreach ($_grades as $gname => $ge) {
            $ge["name"] = $gname; // Add name field to match PA object
            array_push($grades, (object)$ge);
        }
        $this->pset->grades = $grades;
    }

    function runner_output_for($arg) {
        // Note:  argument is ignored, just return the contents of this log file
        $contents = file_get_contents($this->_log_file);
        if (!$contents) {
            throw new Exception("file not found");
        }
        return $contents;
    }

    function commit_list() {
        // Do nothing
        return array();
    }

    function change_grading_commit($arg1, $arg2) {
        // Do nothing
        return;
    }

    function update_commit_notes($d) {
        $this->_notes = $d;
    }

    function to_json() {
        return json_encode($this->_notes);
    }
}


class PaNotesShim {

    static function main($argv) {
        $rest_index = null;
        $long_options = array(
            "pset-json::",
            "log-file::",
            "output::",
            "help::",
        );
        $options = getopt("p::f::h::o::", $long_options, $rest_index);
        //var_dump($options);
        $pos_args = array_slice($argv, $rest_index);
        //var_dump($pos_args);

        if (count($pos_args) != 2) {
            print("Usage:  php" . __FILE__ . "<grading script> <function>\n");
            return 1;
        }

        $script = $pos_args[0];
        $function = $pos_args[1];
        $log_file = $options["f"];
        $output_file = array_key_exists("o", $options) ? $options["o"] : null;
        $pset_json_file = array_key_exists("p", $options) ? $options["p"] : null;

        $Info = new FakePsetInfo($log_file, $pset_json_file);

        // Load and run the script
        require $script;
        $function($Info);

        // Dump the output as JSON
        $notes_json = $Info->to_json();
        if ($output_file) {
            file_put_contents($output_file, $notes_json . "\n");
        } else {
            print($notes_json . "\n");
        }
    }
}

if (realpath($_SERVER["PHP_SELF"]) === __FILE__) {
    exit(PaNotesShim::main($argv));
}

?>
