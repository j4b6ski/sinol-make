# Modified version of https://sinol3.dasie.mimuw.edu.pl/oij/jury/package/-/blob/master/runner.py
# Author of the original code: Bartosz Kostka <kostka@oij.edu.pl>
# Version 0.6 (2021-08-29)
import subprocess
import signal
import threading
from io import StringIO
import glob
from typing import Dict

from sinol_make import contest_types, oiejq
from sinol_make.commands.run.structs import ExecutionResult, ResultChange, ValidationResult, ExecutionData, \
    PointsChange, PrintData
from sinol_make.helpers.parsers import add_compilation_arguments
from sinol_make.interfaces.BaseCommand import BaseCommand
from sinol_make.interfaces.Errors import CompilationError, CheckerOutputException, UnknownContestType
from sinol_make.helpers import compile, compiler, package_util, printer
from sinol_make.structs.status_structs import Status
import sinol_make.util as util
import yaml, os, collections, sys, re, math, dictdiffer
import multiprocessing as mp


def color_memory(memory, limit):
    if memory == -1: return util.color_yellow("")
    memory_str = "%.1fMB" % (memory / 1024.0)
    if memory > limit:
        return util.color_red(memory_str)
    elif memory > limit / 2.0:
        return util.color_yellow(memory_str)
    else:
        return util.color_green(memory_str)


def color_time(time, limit):
    if time == -1: return util.color_yellow("")
    time_str = "%.2fs" % (time / 1000.0)
    if time > limit:
        return util.color_red(time_str)
    elif time > limit / 2.0:
        return util.color_yellow(time_str)
    else:
        return util.color_green(time_str)


def colorize_status(status):
    if status == Status.OK: return util.bold(util.color_green(status))
    if status == Status.PENDING: return util.warning(status)
    return util.error(status)


def update_group_status(group_status, new_status):
    order = [Status.CE, Status.TL, Status.ML, Status.RE, Status.WA, Status.OK]
    if order.index(new_status) < order.index(group_status):
        return new_status
    return group_status


def print_view(term_width, term_height, program_groups_scores, all_results, print_data: PrintData, names, executions,
               groups, scores, tests, possible_score, cpus, hide_memory, config, contest, args):
    width = term_width - 13  # First column has 6 characters, the " | " separator has 3 characters and 4 for margin
    programs_in_row = width // 13  # Each program has 10 characters and the " | " separator has 3 characters

    previous_stdout = sys.stdout
    output = StringIO()
    sys.stdout = output

    program_scores = collections.defaultdict(int)
    # program_times and program_memory are dictionaries of tuples (max, limit),
    # where max is the maximum time/memory used by a program and
    # limit is the time/memory limit of the test that caused the maximum
    # time/memory usage.
    program_times = collections.defaultdict(lambda: (-1, 0))
    program_memory = collections.defaultdict(lambda: (-1, 0))

    time_sum = 0
    for solution in names:
        lang = package_util.get_file_lang(solution)
        for test in tests:
            time_sum += package_util.get_time_limit(test, config, lang, args)

    time_remaining = (len(executions) - print_data.i - 1) * 2 * time_sum / cpus / 1000.0
    title = 'Done %4d/%4d. Time remaining (in the worst case): %5d seconds.' \
            % (print_data.i + 1, len(executions), time_remaining)
    title = title.center(term_width)
    margin = "  "
    for program_ix in range(0, len(names), programs_in_row):
        program_group = names[program_ix:program_ix + programs_in_row]

        def print_table_end():
            print("-" * 8, end="-+-")
            for i in range(len(program_group)):
                if i != len(program_group) - 1:
                    print("-" * 10, end="-+-")
                else:
                    print("-" * 10, end="-+")
            print()

        print_table_end()

        print(margin + "groups", end=" | ")
        for program in program_group:
            print("%10s" % program, end=" | ")
        print()
        print(8 * "-", end=" | ")
        for program in program_group:
            print(10 * "-", end=" | ")
        print()
        for group in groups:
            print(margin + "%6s" % group, end=" | ")
            for program in program_group:
                lang = package_util.get_file_lang(program)
                results = all_results[program][group]
                group_status = Status.OK
                test_scores = []

                for test in results:
                    test_scores.append(results[test].Points)
                    status = results[test].Status
                    if results[test].Time is not None:
                        if program_times[program][0] < results[test].Time:
                            program_times[program] = (results[test].Time, package_util.get_time_limit(test, config,
                                                                                                      lang, args))
                    elif status == Status.TL:
                        program_times[program] = (2 * package_util.get_time_limit(test, config, lang, args),
                                                  package_util.get_time_limit(test, config, lang, args))
                    if results[test].Memory is not None:
                        if program_memory[program][0] < results[test].Memory:
                            program_memory[program] = (results[test].Memory, package_util.get_memory_limit(test, config,
                                                                                                           lang, args))
                    elif status == Status.ML:
                        program_memory[program] = (2 * package_util.get_memory_limit(test, config, lang, args),
                                                   package_util.get_memory_limit(test, config, lang, args))
                    if status == Status.PENDING:
                        group_status = Status.PENDING
                    else:
                        group_status = update_group_status(group_status, status)

                points = contest.get_group_score(test_scores, scores[group])
                if any([results[test].Status == Status.PENDING for test in results]):
                    print(" " * 3 + ("?" * len(str(scores[group]))).rjust(3) +
                          f'/{str(scores[group]).rjust(3)}', end=' | ')
                else:
                    print("%3s" % util.bold(util.color_green(group_status)) if group_status == Status.OK else util.bold(
                        util.color_red(group_status)),
                          "%3s/%3s" % (points, scores[group]),
                          end=" | ")
                program_scores[program] += points if group_status == Status.OK else 0
                program_groups_scores[program][group] = {"status": group_status, "points": points}
            print()
        print(8 * " ", end=" | ")
        for program in program_group:
            print(10 * " ", end=" | ")
        print()
        print(margin + "points", end=" | ")
        for program in program_group:
            print(util.bold("   %3s/%3s" % (program_scores[program], possible_score)), end=" | ")
        print()
        print(margin + "  time", end=" | ")
        for program in program_group:
            program_time = program_times[program]
            print(util.bold(("%20s" % color_time(program_time[0], program_time[1]))
                            if program_time[0] < 2 * program_time[1] and program_time[0] >= 0
                            else "   " + 7 * '-'), end=" | ")
        print()
        print(margin + "memory", end=" | ")
        for program in program_group:
            program_mem = program_memory[program]
            print(util.bold(("%20s" % color_memory(program_mem[0], program_mem[1]))
                            if program_mem[0] < 2 * program_mem[1] and program_mem[0] >= 0
                            else "   " + 7 * '-'), end=" | ")
        print()
        print(8*" ", end=" | ")
        for program in program_group:
            print(10*" ", end=" | ")
        print()

        def print_group_seperator():
            print(8 * "-", end=" | ")
            for program in program_group:
                print(10 * "-", end=" | ")
            print()

        print_group_seperator()

        last_group = None
        for test in tests:
            group = package_util.get_group(test)
            if last_group != group:
                if last_group is not None:
                    print_group_seperator()
                last_group = group

            print(margin + "%6s" % package_util.extract_test_id(test), end=" | ")
            for program in program_group:
                lang = package_util.get_file_lang(program)
                result = all_results[program][package_util.get_group(test)][test]
                status = result.Status
                if status == Status.PENDING: print(10 * ' ', end=" | ")
                else:
                    print("%3s" % colorize_status(status),
                         ("%17s" % color_time(result.Time, package_util.get_time_limit(test, config, lang, args)))
                         if result.Time is not None else 7*" ", end=" | ")
            print()
            if not hide_memory:
                print(8*" ", end=" | ")
                for program in program_group:
                    lang = package_util.get_file_lang(program)
                    result = all_results[program][package_util.get_group(test)][test]
                    print(("%20s" % color_memory(result.Memory, package_util.get_memory_limit(test, config, lang, args)))
                          if result.Memory is not None else 10*" ", end=" | ")
                print()

        print_table_end()
        print()


    sys.stdout = previous_stdout
    return output.getvalue().splitlines(), title, "Use arrows to move."


class Command(BaseCommand):
    """
    Class for running current task
    """


    def get_name(self):
        return 'run'


    def configure_subparser(self, subparser):
        parser = subparser.add_parser(
            'run',
            help='Runs solutions in parallel on tests and verifies the expected solutions\' scores with the config.',
            description='Runs selected solutions (by default all solutions) \
                on selected tests (by default all tests) \
                with a given number of cpus. \
                Measures the solutions\' time with oiejq, unless specified otherwise. \
                After running the solutions, it compares the solutions\' scores with the ones saved in config.yml.'
        )

        default_timetool = 'oiejq' if util.is_linux() else 'time'

        parser.add_argument('-s', '--solutions', type=str, nargs='+',
                            help='solutions to be run, for example prog/abc{b,s}*.{cpp,py}')
        parser.add_argument('-t', '--tests', type=str, nargs='+',
                            help='tests to be run, for example in/abc{0,1}*')
        parser.add_argument('-c', '--cpus', type=int,
                            help='number of cpus to use, you have %d avaliable' % mp.cpu_count())
        parser.add_argument('--tl', type=float, help='time limit for all tests (in s)')
        parser.add_argument('--ml', type=float, help='memory limit for all tests (in MB)')
        parser.add_argument('--hide-memory', dest='hide_memory', action='store_true',
                            help='hide memory usage in report')
        parser.add_argument('-T', '--time-tool', dest='time_tool', choices=['oiejq', 'time'], default=default_timetool,
                            help=f'tool to measure time and memory usage (default: {default_timetool})')
        parser.add_argument('--oiejq-path', dest='oiejq_path', type=str,
                            help='path to oiejq executable (default: `~/.local/bin/oiejq`)')
        add_compilation_arguments(parser)
        parser.add_argument('-a', '--apply-suggestions', dest='apply_suggestions', action='store_true',
                            help='apply suggestions from expected scores report')

    def parse_time(self, time_str):
        if len(time_str) < 3: return -1
        return int(time_str[:-2])


    def parse_memory(self, memory_str):
        if len(memory_str) < 3: return -1
        return int(memory_str[:-2])


    def extract_file_name(self, file_path):
        return os.path.split(file_path)[1]


    def get_group(self, test_path):
        if package_util.extract_test_id(test_path).endswith("ocen"):
            return 0
        return int("".join(filter(str.isdigit, package_util.extract_test_id(test_path))))


    def get_executable_key(self, executable):
        name = package_util.get_file_name(executable)
        value = [0, 0]
        if name[3] == 's':
            value[0] = 1
            suffix = name.split(".")[0][4:]
        elif name[3] == 'b':
            value[0] = 2
            suffix = name.split(".")[0][4:]
        else:
            suffix = name.split(".")[0][3:]
        if suffix != "":
            value[1] = int(suffix)
        return tuple(value)


    def get_solution_from_exe(self, executable):
        file = os.path.splitext(executable)[0]
        for ext in self.SOURCE_EXTENSIONS:
            if os.path.isfile(os.path.join(os.getcwd(), "prog", file + ext)):
                return file + ext
        util.exit_with_error("Source file not found for executable %s" % executable)


    def get_solutions(self, args_solutions):
        if args_solutions is None:
            solutions = [solution for solution in os.listdir("prog/")
                         if self.SOLUTIONS_RE.match(solution)]
            return sorted(solutions, key=self.get_executable_key)
        else:
            solutions = []
            for solution in args_solutions:
                if not os.path.isfile(solution):
                    util.exit_with_error("Solution %s does not exist" % solution)
                if self.SOLUTIONS_RE.match(os.path.basename(solution)) is not None:
                    solutions.append(os.path.basename(solution))
            return sorted(solutions, key=self.get_executable_key)


    def get_executables(self, args_solutions):
        return [package_util.get_executable(solution) for solution in self.get_solutions(args_solutions)]


    def get_possible_score(self, groups):
        possible_score = 0
        for group in groups:
            possible_score += self.scores[group]
        return possible_score


    def get_output_file(self, test_path):
        return os.path.join("out", os.path.split(os.path.splitext(test_path)[0])[1]) + ".out"


    def get_groups(self, tests):
        return sorted(list(set([self.get_group(test) for test in tests])))


    def compile_solutions(self, solutions):
        os.makedirs(self.COMPILATION_DIR, exist_ok=True)
        os.makedirs(self.EXECUTABLES_DIR, exist_ok=True)
        print("Compiling %d solutions..." % len(solutions))
        args = [(solution, True) for solution in solutions]
        with mp.Pool(self.cpus) as pool:
            compilation_results = pool.starmap(self.compile, args)
        return compilation_results


    def compile(self, solution, use_extras = False):
        compile_log_file = os.path.join(
            self.COMPILATION_DIR, "%s.compile_log" % package_util.get_file_name(solution))
        source_file = os.path.join(os.getcwd(), "prog", self.get_solution_from_exe(solution))
        output = os.path.join(self.EXECUTABLES_DIR, package_util.get_executable(solution))

        extra_compilation_args = []
        extra_compilation_files = []
        if use_extras:
            lang = os.path.splitext(source_file)[1][1:]
            for file in self.config.get("extra_compilation_args", {}).get(lang, []):
                extra_compilation_args.append(os.path.join(os.getcwd(), "prog", file))

            for file in self.config.get("extra_compilation_files", []):
                extra_compilation_files.append(os.path.join(os.getcwd(), "prog", file))

        try:
            with open(compile_log_file, "w") as compile_log:
                compile.compile(source_file, output, self.compilers, compile_log, self.args.weak_compilation_flags,
                                extra_compilation_args, extra_compilation_files)
            print(util.info("Compilation of file %s was successful."
                            % package_util.get_file_name(solution)))
            return True
        except CompilationError as e:
            print(util.error("Compilation of file %s was unsuccessful."
                             % package_util.get_file_name(solution)))
            compile.print_compile_log(compile_log_file)
            return False

    def check_output_diff(self, output_file, answer_file):
        """
        Checks whether the output file and the answer file are the same.
        """
        return util.file_diff(output_file, answer_file)

    def check_output_checker(self, name, input_file, output_file, answer_file):
        """
        Checks if the output file is correct with the checker.
        Returns True if the output file is correct, False otherwise and number of points.
        """
        command = [self.checker_executable, input_file, output_file, answer_file]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        process.wait()
        checker_output = process.communicate()[0].decode("utf-8").splitlines()

        if len(checker_output) == 0:
            raise CheckerOutputException("Checker output is empty.")

        if checker_output[0].strip() == "OK":
            points = 100
            if len(checker_output) >= 3:
                try:
                    points = int(checker_output[2].strip())
                except ValueError:
                    pass

            return True, points
        else:
            return False, 0


    def check_output(self, name, input_file, output_file_path, output, answer_file_path):
        """
        Checks if the output file is correct.
        Returns a tuple (is correct, number of points).
        """
        try:
            has_checker = self.checker is not None
        except AttributeError:
            has_checker = False

        if not has_checker:
            with open(answer_file_path, "r") as answer_file:
                correct = util.lines_diff(output, answer_file.readlines())
            return correct, 100 if correct else 0
        else:
            with open(output_file_path, "w") as output_file:
                output_file.write("\n".join(output) + "\n")
            return self.check_output_checker(name, input_file, output_file_path, answer_file_path)


    def execute_oiejq(self, command, name, result_file_path, input_file_path, output_file_path, answer_file_path,
                      time_limit, memory_limit, hard_time_limit):
        env = os.environ.copy()
        env["MEM_LIMIT"] = f'{memory_limit}K'
        env["MEASURE_MEM"] = "1"

        timeout = False
        with open(input_file_path, "r") as input_file:
            process = subprocess.Popen(command, shell=True, stdin=input_file, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=env, preexec_fn=os.setsid)

            def sigint_handler(signum, frame):
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                sys.exit(1)
            signal.signal(signal.SIGINT, sigint_handler)

            try:
                output, lines = process.communicate(timeout=hard_time_limit)
            except subprocess.TimeoutExpired:
                timeout = True
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.communicate()

        result = ExecutionResult()

        if not timeout:
            lines = lines.decode('utf-8').splitlines()
            output = output.decode('utf-8').splitlines()

            for line in lines:
                line = line.strip()
                if ": " in line:
                    (key, value) = line.split(": ")[:2]
                    if key == "Time":
                        result.Time = self.parse_time(value)
                    elif key == "Memory":
                        result.Memory = self.parse_memory(value)
                    else:
                        setattr(result, key, value)

        if timeout:
            result.Status = Status.TL
        elif getattr(result, "Time") is not None and result.Time > time_limit:
            result.Status = Status.TL
        elif getattr(result, "Memory") is not None and result.Memory > memory_limit:
            result.Status = Status.ML
        elif getattr(result, "Status") is None:
            result.Status = Status.RE
        elif result.Status == "OK":  # Here OK is a string, because it is set while parsing oiejq's output.
            if result.Time > time_limit:
                result.Status = Status.TL
            elif result.Memory > memory_limit:
                result.Status = Status.ML
            else:
                try:
                    correct, result.Points = self.check_output(name, input_file_path, output_file_path, output, answer_file_path)
                    if not correct:
                        result.Status = Status.WA
                except CheckerOutputException as e:
                    result.Status = Status.CE
                    result.Error = e.message
        else:
            result.Status = result.Status[:2]

        return result


    def execute_time(self, command, name, result_file_path, input_file_path, output_file_path, answer_file_path,
                      time_limit, memory_limit, hard_time_limit):

        timeout = False
        with open(input_file_path, "r") as input_file:
            process = subprocess.Popen(command, stdin=input_file, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       preexec_fn=os.setsid)

            def sigint_handler(signum, frame):
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                sys.exit(1)
            signal.signal(signal.SIGINT, sigint_handler)

            try:
                output, _ = process.communicate(timeout=hard_time_limit)
            except subprocess.TimeoutExpired:
                timeout = True
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)

        result = ExecutionResult()
        program_exit_code = None
        if not timeout:
            output = output.decode("utf-8").splitlines()
            with open(result_file_path, "r") as result_file:
                lines = result_file.readlines()
            if len(lines) == 3:
                """
                If programs runs successfully, the output looks like this:
                 - first line is CPU time in seconds
                 - second line is memory in KB
                 - third line is exit code
                This format is defined by -f flag in time command.
                """
                result.Time = round(float(lines[0].strip()) * 1000)
                result.Memory = int(lines[1].strip())
                program_exit_code = int(lines[2].strip())
            if len(lines) > 0 and "Command terminated by signal " in lines[0]:
                """
                If there was a runtime error, the first line is the error message with signal number.
                For example:
                    Command terminated by signal 11
                """
                program_exit_code = int(lines[0].strip().split(" ")[-1])

        if program_exit_code is not None and program_exit_code != 0:
            result.Status = Status.RE
        elif timeout:
            result.Status = Status.TL
        elif result.Time > time_limit:
            result.Status = Status.TL
        elif result.Memory > memory_limit:
            result.Status = Status.ML
        else:
            try:
                correct, result.Points = self.check_output(name, input_file_path, output_file_path, output,
                                                           answer_file_path)
                if correct:
                    result.Status = Status.OK
                else:
                    result.Status = Status.WA
            except CheckerOutputException as e:
                result.Status = Status.CE
                result.Error = e.message

        return result


    def run_solution(self, data_for_execution: ExecutionData):
        """
        Run an execution and return the result as ExecutionResult object.
        """

        (name, executable, test, time_limit, memory_limit, timetool_path) = data_for_execution
        file_no_ext = os.path.join(self.EXECUTIONS_DIR, name, package_util.extract_test_id(test))
        output_file = file_no_ext + ".out"
        result_file = file_no_ext + ".res"
        hard_time_limit_in_s = math.ceil(2 * time_limit / 1000.0)

        if self.args.time_tool == 'oiejq':
            command = f'"{timetool_path}" "{executable}"'

            return self.execute_oiejq(command, name, result_file, test, output_file, self.get_output_file(test),
                                      time_limit, memory_limit, hard_time_limit_in_s)
        elif self.args.time_tool == 'time':
            if sys.platform == 'darwin':
                timeout_name = 'gtimeout'
                time_name = 'gtime'
            elif sys.platform == 'linux':
                timeout_name = 'timeout'
                time_name = 'time'
            elif sys.platform == 'win32' or sys.platform == 'cygwin':
                raise Exception("Measuring time with GNU time on Windows is not supported.")

            command = [f'{time_name}', '-f', '%U\\n%M\\n%x', '-o', result_file, executable]
            return self.execute_time(command, name, result_file, test, output_file, self.get_output_file(test),
                                     time_limit, memory_limit, hard_time_limit_in_s)

    def run_solutions(self, compiled_commands, names, solutions):
        """
        Run solutions on tests and print the results as a table to stdout.
        """

        executions = []
        all_results = collections.defaultdict(
            lambda: collections.defaultdict(lambda: collections.defaultdict(map)))
        for (name, executable, result) in compiled_commands:
            lang = package_util.get_file_lang(name)
            if result:
                for test in self.tests:
                    executions.append((name, executable, test, package_util.get_time_limit(test, self.config, lang, self.args),
                                       package_util.get_memory_limit(test, self.config, lang, self.args), self.timetool_path))
                    all_results[name][self.get_group(test)][test] = ExecutionResult(Status.PENDING)
                os.makedirs(os.path.join(self.EXECUTIONS_DIR, name), exist_ok=True)
            else:
                for test in self.tests:
                    all_results[name][self.get_group(test)][test] = ExecutionResult(Status.CE)
        print()
        executions.sort(key = lambda x: (self.get_executable_key(x[1]), x[2]))
        program_groups_scores = collections.defaultdict(dict)
        print_data = PrintData(0)

        has_terminal, terminal_width, terminal_height = util.get_terminal_size()

        if has_terminal:
            run_event = threading.Event()
            run_event.set()
            thr = threading.Thread(target=printer.printer_thread,
                                   args=(run_event, print_view, program_groups_scores, all_results, print_data, names,
                                         executions, self.groups, self.scores, self.tests, self.possible_score,
                                         self.cpus, self.args.hide_memory, self.config, self.contest, self.args))
            thr.start()

        pool = mp.Pool(self.cpus)
        keyboard_interrupt = False
        try:
            for i, result in enumerate(pool.imap(self.run_solution, executions)):
                (name, executable, test, time_limit, memory_limit) = executions[i][:5]
                contest_points = self.contest.get_test_score(result, time_limit, memory_limit)
                result.Points = contest_points
                all_results[name][self.get_group(test)][test] = result
                print_data.i = i
            pool.terminate()
        except KeyboardInterrupt:
            keyboard_interrupt = True
            pool.terminate()
        finally:
            if has_terminal:
                run_event.clear()
                thr.join()

        print("\n".join(print_view(terminal_width, terminal_height, program_groups_scores, all_results, print_data,
                                   names, executions, self.groups, self.scores, self.tests, self.possible_score,
                                   self.cpus, self.args.hide_memory, self.config, self.contest, self.args)[0]))

        if keyboard_interrupt:
            util.exit_with_error("Stopped due to keyboard interrupt.")

        return program_groups_scores, all_results


    def calculate_points(self, results):
        points = 0
        for group, result in results.items():
            if group != 0 and group not in self.scores:
                util.exit_with_error(f'Group {group} doesn\'t have points specified in config file.')
            if isinstance(result, str):
                if result == Status.OK:
                    points += self.scores[group]
            elif isinstance(result, dict):
                points += result["points"]
        return points

    def compile_and_run(self, solutions):
        compilation_results = self.compile_solutions(solutions)
        for i in range(len(solutions)):
            if not compilation_results[i]:
                self.failed_compilations.append(solutions[i])
        os.makedirs(self.EXECUTIONS_DIR, exist_ok=True)
        executables = [os.path.join(self.EXECUTABLES_DIR, package_util.get_executable(solution))
                       for solution in solutions]
        compiled_commands = zip(solutions, executables, compilation_results)
        names = solutions
        return self.run_solutions(compiled_commands, names, solutions)

    def convert_status_to_string(self, dictionary):
        """
        Converts all `Status` enums in dict to strings.
        """
        def _convert(obj):
            if isinstance(obj, dict):
                return { k: _convert(v) for k, v in obj.items() }
            elif isinstance(obj, list):
                return [ _convert(v) for v in obj ]
            elif isinstance(obj, Status):
                return obj.name
            else:
                return obj
        return _convert(dictionary)

    def print_expected_scores(self, expected_scores):
        yaml_dict = { "sinol_expected_scores": self.convert_status_to_string(expected_scores) }
        print(yaml.dump(yaml_dict, default_flow_style=None))

    def get_whole_groups(self):
        """
        Returns a list of groups for which all tests were run.
        """
        group_sizes = {}
        for test in package_util.get_tests():
            group = package_util.get_group(test)
            if group not in group_sizes:
                group_sizes[group] = 0
            group_sizes[group] += 1

        run_group_sizes = {}
        for test in self.tests:
            group = package_util.get_group(test)
            if group not in run_group_sizes:
                run_group_sizes[group] = 0
            run_group_sizes[group] += 1

        whole_groups = []
        for group in group_sizes.keys():
            if group in run_group_sizes and group_sizes[group] == run_group_sizes[group]:
                whole_groups.append(group)
        return whole_groups

    def validate_expected_scores(self, results):
        new_expected_scores = {} # Expected scores based on results

        for solution in results.keys():
            for group in results[solution].keys():
                if group not in self.scores:
                    util.exit_with_error(f'Group {group} doesn\'t have points specified in config file.')

        def convert_to_expected(results):
            new_results = {}
            for solution in results.keys():
                new_results[solution] = {}
                for group, result in results[solution].items():
                    if result["status"] == Status.OK:
                        if result["points"] == self.scores[group]:
                            new_results[solution][group] = Status.OK
                        else:
                            new_results[solution][group] = result
                    else:
                        new_results[solution][group] = result["status"]
            return new_results

        results = convert_to_expected(results)

        if self.checker is None:
            for solution in results.keys():
                new_expected_scores[solution] = {
                    "expected": results[solution],
                    "points": self.calculate_points(results[solution])
                }
        else:
            for solution in results.keys():
                new_expected_scores[solution] = {
                    "expected": results[solution],
                    "points": self.calculate_points(results[solution])
                }

        config_expected_scores = self.config.get("sinol_expected_scores", {})
        used_solutions = results.keys()
        if self.args.solutions == None and config_expected_scores: # If no solutions were specified, use all solutions from config
            used_solutions = config_expected_scores.keys()
        used_solutions = list(used_solutions)

        for solution in self.failed_compilations:
            if solution in used_solutions:
                used_solutions.remove(solution)

        used_groups = set()
        if self.args.tests == None and config_expected_scores: # If no groups were specified, use all groups from config
            for solution in config_expected_scores.keys():
                for group in config_expected_scores[solution]["expected"]:
                    used_groups.add(group)
        else:
            used_groups = self.get_whole_groups()

            # This removes those groups from `new_expected_scores` that have not been run.
            # Then, if there are any solutions for which no groups have been run, they are also removed.
            solutions_to_delete = []
            for solution in new_expected_scores.keys():
                groups_to_remove = []
                for group in new_expected_scores[solution]["expected"]:
                    if group not in used_groups:
                        groups_to_remove.append(group)
                for group in groups_to_remove:
                    del new_expected_scores[solution]["expected"][group]

                # If there are no groups left, remove the solution.
                if len(new_expected_scores[solution]["expected"]) == 0:
                    solutions_to_delete.append(solution)
            for solution in solutions_to_delete:
                del new_expected_scores[solution]

        used_groups = list(used_groups)

        expected_scores = {} # Expected scores from config with only solutions and groups that were run
        for solution in used_solutions:
            if solution in config_expected_scores.keys():
                expected_scores[solution] = {
                    "expected": {},
                    "points": 0
                }

                for group in used_groups:
                    if group in config_expected_scores[solution]["expected"]:
                        expected_scores[solution]["expected"][group] = config_expected_scores[solution]["expected"][group]

                expected_scores[solution]["points"] = self.calculate_points(expected_scores[solution]["expected"])
                if len(expected_scores[solution]["expected"]) == 0:
                    del expected_scores[solution]

        if self.args.tests is not None:
            print("Showing expected scores only for groups with all tests run.")
        print(util.bold("Expected scores from config:"))
        self.print_expected_scores(expected_scores)
        print(util.bold("\nExpected scores based on results:"))
        self.print_expected_scores(new_expected_scores)

        expected_scores_diff = dictdiffer.diff(expected_scores, new_expected_scores)
        added_solutions = set()
        removed_solutions = set()
        added_groups = set()
        removed_groups = set()
        changes = []

        for type, field, change in list(expected_scores_diff):
            if type == "add":
                if field == '': # Solutions were added
                    for solution in change:
                        added_solutions.add(solution[0])
                elif field[1] == "expected": # Groups were added
                    for group in change:
                        added_groups.add(group[0])
            elif type == "remove":
                # We check whether a solution was removed only when sinol_make was run on all of them
                if field == '' and self.args.solutions == None and config_expected_scores:
                    for solution in change:
                        removed_solutions.add(solution[0])
                # We check whether a group was removed only when sinol_make was run on all of them
                elif field[1] == "expected" and self.args.tests == None and config_expected_scores:
                    for group in change:
                        removed_groups.add(group[0])
            elif type == "change":
                if field[1] == "expected": # Results for at least one group has changed
                    solution = field[0]
                    group = field[2]
                    if isinstance(change[0], str) and isinstance(change[1], dict):
                        changes.append(PointsChange(
                            solution=solution,
                            group=group,
                            old_points=self.scores[field[2]],
                            new_points=change[1]["points"]
                        ))
                    elif isinstance(change[0], dict) and isinstance(change[1], str):
                        changes.append(PointsChange(
                            solution=solution,
                            group=group,
                            old_points=change[0]["points"],
                            new_points=self.scores[field[2]]
                        ))
                    elif isinstance(change[0], dict) and isinstance(change[1], dict):
                        changes.append(PointsChange(
                            solution=solution,
                            group=group,
                            old_points=change[0]["points"],
                            new_points=change[1]["points"]
                        ))
                    else:
                        changes.append(ResultChange(
                            solution=solution,
                            group=group,
                            old_result=change[0],
                            result=change[1]
                        ))

        return ValidationResult(
            added_solutions,
            removed_solutions,
            added_groups,
            removed_groups,
            changes,
            expected_scores,
            new_expected_scores
        )


    def print_expected_scores_diff(self, validation_results: ValidationResult):
        diff = validation_results
        config_expected_scores = self.config.get("sinol_expected_scores", {})

        def warn_if_not_empty(set, message):
            if len(set) > 0:
                print(util.warning(message + ": "), end='')
                print(util.warning(", ".join([str(x) for x in set])))

        warn_if_not_empty(diff.added_solutions, "Solutions were added")
        warn_if_not_empty(diff.removed_solutions, "Solutions were removed")
        warn_if_not_empty(diff.added_groups, "Groups were added")
        warn_if_not_empty(diff.removed_groups, "Groups were removed")

        for change in diff.changes:
            def print_points_change(solution, group, new_points, old_points):
                print(util.warning("Solution %s passed group %d with %d points while it should pass with %d points." %
                                   (solution, group, new_points, old_points)))

            if isinstance(change, ResultChange):
                if isinstance(change.result, str):
                    print(util.warning("Solution %s passed group %d with status %s while it should pass with status %s." %
                                       (change.solution, change.group, change.result, change.old_result)))
                elif isinstance(change.result, int):
                    print_points_change(change.solution, change.group, change.result, change.old_result)
            elif isinstance(change, PointsChange):
                print_points_change(change.solution, change.group, change.new_points, change.old_points)

        if diff.expected_scores == diff.new_expected_scores:
            print(util.info("Expected scores are correct!"))
        else:
            def delete_group(solution, group):
                if group in config_expected_scores[solution]["expected"]:
                    del config_expected_scores[solution]["expected"][group]
                    config_expected_scores[solution]["points"] = self.calculate_points(config_expected_scores[solution]["expected"])

            def set_group_result(solution, group, result):
                config_expected_scores[solution]["expected"][group] = result
                config_expected_scores[solution]["points"] = self.calculate_points(config_expected_scores[solution]["expected"])


            if self.args.apply_suggestions:
                for solution in diff.removed_solutions:
                    del config_expected_scores[solution]

                for solution in config_expected_scores:
                    for group in diff.removed_groups:
                        delete_group(solution, group)

                for solution in diff.new_expected_scores.keys():
                    if solution in config_expected_scores:
                        for group, result in diff.new_expected_scores[solution]["expected"].items():
                            set_group_result(solution, group, result)
                    else:
                        config_expected_scores[solution] = diff.new_expected_scores[solution]


                self.config["sinol_expected_scores"] = self.convert_status_to_string(config_expected_scores)
                util.save_config(self.config)
                print(util.info("Saved suggested expected scores description."))
            else:
                util.exit_with_error("Use flag --apply-suggestions to apply suggestions.")


    def set_constants(self):
        self.ID = package_util.get_task_id()
        self.TMP_DIR = os.path.join(os.getcwd(), "cache")
        self.COMPILATION_DIR = os.path.join(self.TMP_DIR, "compilation")
        self.EXECUTIONS_DIR = os.path.join(self.TMP_DIR, "executions")
        self.EXECUTABLES_DIR = os.path.join(self.TMP_DIR, "executables")
        self.SOURCE_EXTENSIONS = ['.c', '.cpp', '.py', '.java']
        self.PROGRAMS_IN_ROW = 8
        self.SOLUTIONS_RE = re.compile(r"^%s[bs]?[0-9]*\.(cpp|cc|java|py|pas)$" % self.ID)


    def validate_arguments(self, args):
        compilers = compiler.verify_compilers(args, self.get_solutions(None))

        timetool_path = None
        if args.time_tool == 'oiejq':
            if not util.is_linux():
                util.exit_with_error('As `oiejq` works only on Linux-based operating systems,\n'
                                     'we do not recommend using operating systems such as Windows or macOS.\n'
                                     'Nevertheless, you can still run sinol-make by specifying\n'
                                     'another way of measuring time through the `--time-tool` flag.\n'
                                     'See `sinol-make run --help` for more information about the flag.\n'
                                     'See https://github.com/sio2project/sinol-make#why for more information about `oiejq`.\n')

            oiejq.check_perf_counters_enabled()
            if 'oiejq_path' in args and args.oiejq_path is not None:
                if not oiejq.check_oiejq(args.oiejq_path):
                    util.exit_with_error('Invalid oiejq path.')
                timetool_path = args.oiejq_path
            else:
                timetool_path = oiejq.get_oiejq_path()
            if timetool_path is None:
                util.exit_with_error('oiejq is not installed.')
        elif args.time_tool == 'time':
            if sys.platform == 'win32' or sys.platform == 'cygwin':
                util.exit_with_error('Measuring with `time` is not supported on Windows.')
            timetool_path = 'time'

        return compilers, timetool_path

    def exit(self):
        if len(self.failed_compilations) > 0:
            util.exit_with_error('Compilation failed for {cnt} solution{letter}.'.format(
                cnt=len(self.failed_compilations), letter='' if len(self.failed_compilations) == 1 else 's'))

    def set_scores(self):
        self.tests = package_util.get_tests(self.args.tests)
        self.groups = self.get_groups(self.tests)
        self.scores = collections.defaultdict(int)

        if 'scores' not in self.config.keys():
            print(util.warning('Scores are not defined in config.yml. Points will be assigned equally to all groups.'))
            num_groups = len(self.groups)
            self.scores = {}
            if self.groups[0] == 0:
                num_groups -= 1
                self.scores[0] = 0

            points_per_group = 100 // num_groups
            for group in self.groups:
                if group == 0:
                    continue
                self.scores[group] = points_per_group

            if points_per_group * num_groups != 100:
                self.scores[self.groups[-1]] += 100 - points_per_group * num_groups

            print("Points will be assigned as follows:")
            total_score = 0
            for group in self.scores:
                print("%2d: %3d" % (group, self.scores[group]))
                total_score += self.scores[group]
            print()
        else:
            total_score = 0
            for group in self.config["scores"]:
                self.scores[group] = self.config["scores"][group]
                total_score += self.scores[group]

            if total_score != 100:
                print(util.warning("WARN: Scores sum up to %d instead of 100." % total_score))
                print()

        self.possible_score = self.get_possible_score(self.groups)

    def get_valid_input_files(self):
        """
        Returns list of input files that have corresponding output file.
        """
        output_tests = glob.glob(os.path.join(os.getcwd(), "out", "*.out"))
        output_tests_ids = [package_util.extract_test_id(test) for test in output_tests]
        valid_input_files = []
        for test in self.tests:
            if package_util.extract_test_id(test) in output_tests_ids:
                valid_input_files.append(test)
        return valid_input_files

    def validate_existence_of_outputs(self):
        """
        Checks if all input files have corresponding output files.
        """
        valid_input_files = self.get_valid_input_files()
        if len(valid_input_files) != len(self.tests):
            missing_tests = list(set(self.tests) - set(valid_input_files))
            missing_tests.sort()

            print(util.warning('Missing output files for tests: ' + ', '.join(
                [self.extract_file_name(test) for test in missing_tests])))
            print(util.warning('Running only on tests with output files.'))
            self.tests = valid_input_files
            self.groups = self.get_groups(self.tests)

    def check_are_any_tests_to_run(self):
        """
        Checks if there are any tests to run and prints them and checks
        if all input files have corresponding output files.
        """
        if len(self.tests) > 0:
            print(util.bold('Tests that will be run:'), ' '.join([self.extract_file_name(test) for test in self.tests]))

            example_tests = [test for test in self.tests if self.get_group(test) == 0]
            if len(example_tests) == len(self.tests):
                print(util.warning('Running only on example tests.'))

            if not self.has_lib:
                self.validate_existence_of_outputs()
        else:
            print(util.warning('There are no tests to run.'))

    def check_errors(self, results: Dict[str, Dict[str, Dict[str, ExecutionResult]]]):
        """
        Checks if there were any errors during execution and exits if there were.
        :param results: Dictionary of results.
        :return:
        """
        error_msg = ""
        for solution in results:
            for group in results[solution]:
                for test in results[solution][group]:
                    if results[solution][group][test].Status == Status.CE and \
                       results[solution][group][test].Error is not None:
                        error_msg += f'Solution {solution} had an error on test {test}: {results[solution][group][test].Error}\n'
        if error_msg != "":
            util.exit_with_error(error_msg)

    def run(self, args):
        util.exit_if_not_package()

        self.set_constants()
        self.args = args
        with open(os.path.join(os.getcwd(), "config.yml"), 'r') as config:
            try:
                self.config = yaml.load(config, Loader=yaml.FullLoader)
            except AttributeError:
                self.config = yaml.load(config)

        try:
            self.contest = contest_types.get_contest_type()
        except UnknownContestType as e:
            util.exit_with_error(str(e))

        if not 'title' in self.config.keys():
            util.exit_with_error('Title was not defined in config.yml.')

        self.compilers, self.timetool_path = self.validate_arguments(args)

        title = self.config["title"]
        print("Task: %s (tag: %s)" % (title, self.ID))
        self.cpus = args.cpus or mp.cpu_count()

        checker = glob.glob(os.path.join(os.getcwd(), "prog", f'{self.ID}chk.*'))
        if len(checker) != 0:
            print(util.info("Checker found: %s" % os.path.basename(checker[0])))
            self.checker = checker[0]
            checker_basename = os.path.basename(self.checker)
            self.checker_executable = os.path.join(self.EXECUTABLES_DIR, os.path.splitext(checker_basename)[0] + ".e")

            checker_compilation = self.compile_solutions([self.checker])
            if not checker_compilation[0]:
                util.exit_with_error('Checker compilation failed.')
        else:
            self.checker = None

        lib = glob.glob(os.path.join(os.getcwd(), "prog", f'{self.ID}lib.*'))
        self.has_lib = len(lib) != 0

        self.set_scores()
        self.check_are_any_tests_to_run()
        self.failed_compilations = []
        solutions = self.get_solutions(self.args.solutions)

        for solution in solutions:
            lang = package_util.get_file_lang(solution)
            for test in self.tests:
                # The functions will exit if the limits are not set
                _ = package_util.get_time_limit(test, self.config, lang, self.args)
                _ = package_util.get_memory_limit(test, self.config, lang, self.args)

        results, all_results = self.compile_and_run(solutions)
        self.check_errors(all_results)
        validation_results = self.validate_expected_scores(results)
        self.print_expected_scores_diff(validation_results)
        self.exit()
