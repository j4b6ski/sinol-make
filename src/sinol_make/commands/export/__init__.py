import os
import glob
import shutil
import tarfile
import argparse
import yaml

from sinol_make import util
from sinol_make.helpers import package_util, parsers, paths
from sinol_make.commands.gen import gen_util
from sinol_make.interfaces.BaseCommand import BaseCommand


class Command(BaseCommand):
    """
    Class for "export" command.
    """

    def get_name(self):
        return "export"

    def configure_subparser(self, subparser: argparse.ArgumentParser):
        parser = subparser.add_parser(
            self.get_name(),
            help='Create archive for oioioi upload',
            description='Creates archive in the current directory ready to upload to sio2 or szkopul.')
        parsers.add_compilation_arguments(parser)

    def get_generated_tests(self):
        """
        Returns list of generated tests.
        Executes ingen to check what tests are generated.
        """
        if not gen_util.ingen_exists(self.task_id):
            return []

        working_dir = paths.get_cache_path('export', 'tests')
        if os.path.exists(working_dir):
            shutil.rmtree(working_dir)
        os.makedirs(working_dir)

        ingen_path = gen_util.get_ingen(self.task_id)
        ingen_exe = gen_util.compile_ingen(ingen_path, self.args, self.args.weak_compilation_flags)
        if not gen_util.run_ingen(ingen_exe, working_dir):
            util.exit_with_error('Failed to run ingen.')

        tests = glob.glob(os.path.join(working_dir, f'{self.task_id}*.in'))
        return [package_util.extract_test_id(test) for test in tests]

    def copy_package_required_files(self, target_dir: str):
        """
        Copies package files and directories from
        current directory to target directory.
        :param target_dir: Directory to copy files to.
        """
        files = ['config.yml', 'makefile.in', 'Makefile.in',
                 'prog', 'doc', 'attachments', 'dlazaw']
        for file in files:
            file_path = os.path.join(os.getcwd(), file)
            if os.path.exists(file_path):
                if os.path.isdir(file_path):
                    shutil.copytree(file_path, os.path.join(target_dir, file))
                else:
                    shutil.copy(file_path, target_dir)

        print('Copying example tests...')
        for ext in ['in', 'out']:
            os.mkdir(os.path.join(target_dir, ext))
            for test in glob.glob(os.path.join(os.getcwd(), ext, f'{self.task_id}0*.{ext}')):
                shutil.copy(test, os.path.join(target_dir, ext))

        print('Generating tests...')
        generated_tests = self.get_generated_tests()
        tests_to_copy = []
        for ext in ['in', 'out']:
            for test in glob.glob(os.path.join(os.getcwd(), ext, f'{self.task_id}*.{ext}')):
                if package_util.extract_test_id(test) not in generated_tests:
                    tests_to_copy.append(test)

        if len(tests_to_copy) > 0:
            print(util.warning(f'Found {len(tests_to_copy)} tests that are not generated by ingen.'))
            for test in tests_to_copy:
                print(util.warning(f'Coping {os.path.basename(test)}...'))
                shutil.copy(test, os.path.join(target_dir, os.path.splitext(os.path.basename(test))[1]))

    def create_makefile_in(self, target_dir: str, config: dict):
        """
        Creates required `makefile.in` file.
        :param target_dir: Directory to create files in.
        :param config: Config dictionary.
        """
        with open(os.path.join(target_dir, 'makefile.in'), 'w') as f:
            cxx_flags = '-std=c++17'
            c_flags = '-std=c17'
            def format_multiple_arguments(obj):
                if isinstance(obj, str):
                    return obj
                return ' '.join(obj)

            if 'extra_compilation_args' in config:
                if 'cpp' in config['extra_compilation_args']:
                    cxx_flags += ' ' + format_multiple_arguments(config['extra_compilation_args']['cpp'])
                if 'c' in config['extra_compilation_args']:
                    c_flags += ' ' + format_multiple_arguments(config['extra_compilation_args']['c'])

            f.write(f'MODE = wer\n'
                    f'ID = {self.task_id}\n'
                    f'SIG = sinolmake\n'
                    f'\n'
                    f'TIMELIMIT = {config["time_limit"]}\n'
                    f'SLOW_TIMELIMIT = {4 * config["time_limit"]}\n'
                    f'MEMLIMIT = {config["memory_limit"]}\n'
                    f'\n'
                    f'OI_TIME = oiejq\n'
                    f'\n'
                    f'CXXFLAGS += {cxx_flags}\n'
                    f'CFLAGS += {c_flags}\n')

    def compress(self, target_dir):
        """
        Compresses target directory to archive.
        :param target_dir: Target directory path.
        :return: Path to archive.
        """
        archive = os.path.join(os.getcwd(), f'{self.task_id}.tgz')
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(target_dir, arcname=os.path.basename(target_dir))
        return archive

    def run(self, args: argparse.Namespace):
        util.exit_if_not_package()

        self.args = args
        self.task_id = package_util.get_task_id()

        with open(os.path.join(os.getcwd(), 'config.yml'), 'r') as config_file:
            config = yaml.load(config_file, Loader=yaml.FullLoader)

        export_package_path = paths.get_cache_path('export', self.task_id)
        if os.path.exists(export_package_path):
            shutil.rmtree(export_package_path)
        os.makedirs(export_package_path)

        util.change_stack_size_to_unlimited()
        self.copy_package_required_files(export_package_path)
        self.create_makefile_in(export_package_path, config)
        archive = self.compress(export_package_path)

        print(util.info(f'Exported to {self.task_id}.tgz'))
