# encoding: utf-8

import os
import re
import sys
import operator
import argparse
import itertools
import subprocess

from datetime import datetime
from distutils.version import LooseVersion

import requests

from redbaron import RedBaron


def find_pkginfo(path):
    dirs = os.listdir(path)

    if "__pkginfo__.py" in dirs:
        return os.path.join(path, "__pkginfo__.py")

    cube_subdirs = [os.path.join(path, x) for x in dirs if os.path.isdir(x) and x.startswith("cubicweb_")]

    if not cube_subdirs:
        print("Couldn't find the __pkginfo__.py file :(")
        sys.exit(1)

    for subdir in cube_subdirs:
        if "__pkginfo__.py" in os.listdir(subdir):
            return os.path.join(subdir, "__pkginfo__.py")

    print("Couldn't find the __pkginfo__.py file :(")
    sys.exit(1)


def parse_pkginfo(path):
    red = RedBaron(open(path, "r").read())

    depends = red.find("assign", lambda x: x.target.value == "__depends__")

    if not depends:
        print("I couldn't find __depends__ in the __pkginfo__.py :(")
        sys.exit(1)

    assert depends.value.type == "dict"

    # oupsi I'm lazy, it's bad :(
    # XXX use to_python() somehow
    return eval(depends.value.dumps()), red, depends


def merge_depends_with_pypi_info(depends):
    new_depends = {}

    for key, value in depends.items():
        pkg_name = key.split("[", 1)[0]

        print("Get all releases of %s..." % pkg_name)
        response = requests.get("https://pypi.org/pypi/%s/json" % pkg_name, timeout=30)
        if response.status_code == 404:
            print("Warning: %s doesn't exist on pypi, skip it" % pkg_name)
            continue

        data = response.json()

        new_depends[key] = {
            "pkg_name": pkg_name,
            "current_version_scheme": value,
            "all_versions": data["releases"].keys(),
        }

    return new_depends


def filter_pkg_that_can_be_upgraded(depends):
    no_upgrades = []
    new_depends = {}

    for key, value in depends.items():
        conditions = parse_conditions(value["current_version_scheme"])

        if conditions is None:
            print("No specified version for %s, drop it" % key)
            continue

        compatible_versions = value["all_versions"]

        for (op, version) in conditions:
            compatible_versions = [x for x in compatible_versions if op(LooseVersion(x), LooseVersion(version))]

        maximum_version = list(sorted(map(LooseVersion, compatible_versions)))[-1]
        all_versions_sorted = sorted(map(LooseVersion, value["all_versions"]))
        possible_upgrades = list(itertools.dropwhile(lambda x: x <= maximum_version, all_versions_sorted))

        if possible_upgrades:
            new_depends[key] = value
            new_depends[key]["possible_upgrades"] = possible_upgrades
        else:
            no_upgrades.append(key)

    if no_upgrades:
        print("Skipped packages that don't need to be upgraded: %s" % (", ".join(no_upgrades)))

    if new_depends:
        print("")
        print("Packages that can upgrades with all those available verisons:")
        for key, value in new_depends.items():
            print("* %s (%s) to %s" % (key, value["current_version_scheme"], ", ".join(map(lambda x: x.vstring, value["possible_upgrades"]))))

    return new_depends


def parse_conditions(conditions):
    string_to_operator = {
        "==": operator.eq,
        "<": operator.lt,
        "<=": operator.le,
        "!=": operator.ne,
        ">=": operator.ge,
        ">": operator.gt,
    }

    parsed_conditions = []

    if not conditions:
        return None

    for i in conditions.split(","):
        version_operator, version_number = re.match("(==|>=|<=|>|<) *([0-9.]*)", i.strip()).groups()

        parsed_conditions.append([
            string_to_operator[version_operator],
            version_number,
        ])

    return parsed_conditions


def try_to_upgrade_dependencies(test_command, depends, pkginfo_path, red, red_depends):
    def change_dependency_version_on_disk(entry, value):
        entry.value = ("'== %s'" % value).encode("Utf-8")

        dumps = red.dumps()
        with open(pkginfo_path, "w") as pkginfo_file:
            pkginfo_file.write(dumps)

    def hg_commit(key, before, after):
        commit_message = "[enh] upgrade %s from '%s' to '== %s'" % (key, before, after)
        hg_commit_command = "hg commit -m \"%s\"" % commit_message
        print(hg_commit_command)
        subprocess.check_call(hg_commit_command, shell=True)

        return commit_message

    def launch_test_command(test_command, depend_key, before, after):
        print("starting test process '%s'..." % test_command)
        log_file_name = "autoupgradedependencies/%s/upgrade_%s_from_%s_to_%s.log" % (session_start_time,
                                                                                     depend_key, before, after)
        log_file_name = log_file_name.replace(" ", "")

        directory = os.path.split(log_file_name)[0]

        log_file_name = os.path.realpath(log_file_name)

        if not os.path.exists(directory):
            os.makedirs(directory)

        print("logging command output in %s" % log_file_name)
        test_process = subprocess.Popen(test_command,
                                        shell=True,
                                        bufsize=0,
                                        stdout=open(log_file_name, "w"),
                                        stderr=subprocess.STDOUT)

        # will return return_code
        return test_process.wait(), log_file_name

    # start with cubes
    cubes = filter(lambda x: x[0].startswith("cubicweb-"), depends.items())
    not_cubes = filter(lambda x: not x[0].startswith("cubicweb-"), depends.items())

    session_start_time = datetime.now().strftime("%F-%X")

    summary = {
        "full_success": [],
        "partial_success": [],
        "total_failure": [],
        "commits": [],
    }

    for depend_key, depend_data in itertools.chain(cubes, not_cubes):
        entry = red_depends.value.filter(lambda x: hasattr(x, "key") and x.key.to_python() == depend_key)[0]

        initial_value = entry.value.copy()

        max_possible_value = depend_data["possible_upgrades"][-1].vstring

        print("")
        print("Upgrading %s to %s" % (depend_key, max_possible_value))
        change_dependency_version_on_disk(entry, max_possible_value)

        pid, log_file_name = launch_test_command(test_command, depend_key, initial_value.to_python(), max_possible_value)
        if pid == 0:
            print("Success for upgrading %s to %s!" % (depend_key, max_possible_value))
            summary["commits"].append(hg_commit(depend_key, initial_value.to_python(), max_possible_value))

            summary["full_success"].append({
                "dependency": depend_key,
                "from": initial_value.to_python(),
                "to": max_possible_value,
                "log_file_name": log_file_name,
            })

        elif len(depend_data["possible_upgrades"]) > 1:
            print("Failure when upgrading %s to %s, switch to version per version strategy" % (depend_key, max_possible_value))

            previous_version = None

            for number, version in enumerate(depend_data["possible_upgrades"][:-1]):
                version = version.vstring

                print("")
                print("trying %s to %s" % (depend_key, version))
                change_dependency_version_on_disk(entry, version)

                pid, log_file_name = launch_test_command(test_command, depend_key, initial_value.to_python(), version)
                if pid == 0:
                    print("Success on %s for version %s! Continue to next version" % (depend_key, version))
                    previous_version = version
                elif previous_version:
                    print("Failure when upgrading %s to %s, %s is the maximum upgradable version" % (depend_key, version, previous_version))

                    change_dependency_version_on_disk(entry, previous_version)

                    summary["commits"].append(hg_commit(depend_key, initial_value.to_python(), version))

                    summary["partial_success"].append({
                        "dependency": depend_key,
                        "from": initial_value.to_python(),
                        "to": version,
                        "log_file_name": log_file_name,
                        "possible_upgrades": depend_data["possible_upgrades"][number:],
                    })

                    break
                else:
                    print("Failure when upgrading %s to any version, it's not upgradable :(" % (depend_key))

                    entry.value = initial_value

                    dumps = red.dumps()
                    with open(pkginfo_path, "w") as pkginfo_file:
                        pkginfo_file.write(dumps)

                    summary["total_failure"].append({
                        "dependency": depend_key,
                        "from": initial_value.to_python(),
                        "log_file_name": log_file_name,
                        "possible_upgrades": depend_data["possible_upgrades"],
                    })

                    break
            # we haven't break
            # yes this python syntaxe is horrible
            else:
                print("Actually it's the last compatible versions before the buggy %s" % max_possible_value)
                # should already be done
                # change_dependency_version_on_disk(entry, previous_version)

                summary["commits"].append(hg_commit(depend_key, initial_value.to_python(), version))

                summary["partial_success"].append({
                    "dependency": depend_key,
                    "from": initial_value.to_python(),
                    "to": version,
                    "log_file_name": log_file_name,
                    "possible_upgrades": depend_data["possible_upgrades"][-1:],
                })

        else:
            print("Failure when upgrading %s to %s, fail back to previous value :(" % (depend_key, max_possible_value))
            entry.value = initial_value

            dumps = red.dumps()
            with open(pkginfo_path, "w") as pkginfo_file:
                pkginfo_file.write(dumps)

            summary["total_failure"].append({
                "dependency": depend_key,
                "from": initial_value.to_python(),
                "log_file_name": log_file_name,
                "possible_upgrades": [],
            })

    print("")
    print("Summary of execution")
    print("====================")

    if summary["full_success"]:
        print("")
        print("Successful upgrade to latest version:")

        for i in summary["full_success"]:
            print("* %s from '%s' to %s, log: %s" % (i["dependency"], i["from"], i["to"], i["log_file_name"]))

    if summary["partial_success"]:
        print("")
        print("Upgraded to a more up to date version but fail to upgrade to the latest one:")

        for i in summary["full_success"]:
            print("* %s from '%s' to %s, newest versions: %s, log: %s" % (i["dependency"],
                                                                          i["from"], i["to"],
                                                                          map(lambda x: x.vstring, i["possible_upgrades"]),
                                                                          i["log_file_name"]))

    if summary["total_failure"]:
        print("")
        print("Totally fail to upgrade to any version")

        for i in summary["total_failure"]:
            print("* %s, possible upgrades: %s, log: %s" % (i["dependency"], i["from"],
                                                            map(lambda x: x.vstring, i["possible_upgrades"]),
                                                            i["log_file_name"]))

    print("")
    if summary["commits"]:
        print("Generated commits:")
        for i in summary["commits"]:
            print("* %s" % i)
    else:
        print("Not commits.")

    print("")
    print("All log files are located in %s" % os.path.split(log_file_name)[0])


def main():
    parser = argparse.ArgumentParser(description='Auto upgrade a cube dependencies that passes tests.')
    parser.add_argument('test_command', help='test command to launch, for example "tox --recreate -e py27"')

    args = parser.parse_args()

    if args.test_command.strip().startswith("tox") and "--recreate" not in args.test_command:
        print("WARNING: if you are using tox you very likely want to put '--recreate' in the command")

    if subprocess.check_output(["hg", "diff"]) != '':
        print("ERROR: according to 'hg diff' repository is not clean, abort")
        sys.exit(1)

    path = "."
    path = os.path.realpath(os.path.expanduser(path))

    pkginfo_path = find_pkginfo(path)
    print("Foudn __pkginfo__.py: %s" % pkginfo_path)

    depends, red, red_depends = parse_pkginfo(pkginfo_path)
    cubes = [x for x in depends if x.startswith("cubicweb-")]

    print("")

    if cubes:
        sys.stdout.write("Found cubes:\n* ")
        print("\n* ".join(cubes))
    else:
        print("This cube doesn't depends on other cubes")

    print("")

    depends = merge_depends_with_pypi_info(depends)

    print("")

    depends = filter_pkg_that_can_be_upgraded(depends)

    if not depends:
        print("")
        print("Nothing to do, everything is up to date")
        sys.exit(0)

    try_to_upgrade_dependencies(args.test_command, depends, pkginfo_path, red, red_depends)


if __name__ == '__main__':
    main()
