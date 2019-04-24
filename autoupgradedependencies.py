# encoding: utf-8

import os
import re
import sys
import operator
import itertools
import subprocess

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
        data = requests.get("https://pypi.org/pypi/%s/json" % pkg_name).json()
        new_depends[key] = {
            "pkg_name": pkg_name,
            "current_version_scheme": value,
            "all_versions": data["releases"].keys(),
        }

    return new_depends


def filter_pkg_that_can_be_upgraded(depends):
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
            print("%s can be upgraded to %s" % (key, ", ".join([x.vstring for x in possible_upgrades])))
            new_depends[key] = value
            new_depends[key]["possible_upgrades"] = possible_upgrades
        else:
            print("No possible upgrades for %s, drop it" % key)

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
        entry.value = "'== %s'" % value

        dumps = red.dumps()
        with open(pkginfo_path, "w") as pkginfo_file:
            pkginfo_file.write(dumps)

    # start with cubes
    for depend_key, depend_data in filter(lambda x: x[0].startswith("cubicweb-"), depends.items()):
        entry = red_depends.value.filter(lambda x: hasattr(x, "key") and x.key.to_python() == depend_key)[0]

        initial_value = entry.value.copy()

        max_possible_value = depend_data["possible_upgrades"][-1].vstring

        print("")
        print("Upgrading %s to %s" % (depend_key, max_possible_value))
        change_dependency_version_on_disk(entry, max_possible_value)

        print("starting test process '%s'..." % test_command)
        delimiter = "=" * len("starting test process '%s'..." % test_command)
        print(delimiter)
        test_process = subprocess.Popen(test_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        for i in test_process.stdout:
            sys.stdout.write(i)

        return_code = test_process.wait()
        print(delimiter)

        if return_code == 0:
            print("Success for upgrading %s to %s!" % (depend_key, max_possible_value))

            hg_commit_command = "hg commit -m \"[enh] upgrade %s from '%s' to '== %s'\"" % (depend_key, initial_value.to_python(), max_possible_value)
            print(hg_commit_command)
            subprocess.check_call(hg_commit_command, shell=True)
        elif len(depend_data["possible_upgrades"]) > 1:
            print("Failure when upgrading %s to %s, switch to version per version strategy" % (depend_key, max_possible_value))

            previous_version = None

            for version in depend_data["possible_upgrades"][:-1]:
                version = version.vstring

                print("")
                print("trying %s to %s" % (depend_key, version))
                change_dependency_version_on_disk(entry, version)

                print("starting test process '%s'..." % test_command)
                delimiter = "=" * len("starting test process '%s'..." % test_command)
                print(delimiter)
                test_process = subprocess.Popen(test_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

                for i in test_process.stdout:
                    sys.stdout.write(i)

                return_code = test_process.wait()
                print(delimiter)

                if return_code == 0:
                    print("Success on %s for version %s! Continue to next version" % (depend_key, version))
                    previous_version = version
                elif previous_version:
                    print("Failure when upgrading %s to %s, %s is the maximum upgradable version" % (depend_key, version, previous_version))

                    change_dependency_version_on_disk(entry, previous_version)

                    hg_commit_command = "hg commit -m \"[enh] upgrade %s from '%s' to '== %s'\"" % (depend_key, initial_value.to_python(), version)
                    print(hg_commit_command)
                    subprocess.check_call(hg_commit_command, shell=True)

                    break
                else:
                    print("Failure when upgrading %s to any version, it's not upgradable :(" % (depend_key))

                    entry.value = initial_value

                    dumps = red.dumps()
                    with open(pkginfo_path, "w") as pkginfo_file:
                        pkginfo_file.write(dumps)

                    break
            # we haven't break
            # yes this python syntaxe is horrible
            else:
                print("Actually it's the last compatible versions before the buggy %s" % max_possible_value)
                # should already be done
                # change_dependency_version_on_disk(entry, previous_version)
                hg_commit_command = "hg commit -m \"[enh] upgrade %s from '%s' to '== %s'\"" % (depend_key, initial_value.to_python(), version)
                print(hg_commit_command)
                subprocess.check_call(hg_commit_command, shell=True)

        else:
            print("Failure when upgrading %s to %s, fail back to previous value :(" % (depend_key, max_possible_value))
            entry.value = initial_value

            dumps = red.dumps()
            with open(pkginfo_path, "w") as pkginfo_file:
                pkginfo_file.write(dumps)


def main():
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

    test_command = "sleep 2; [ $(($RANDOM % 2)) -eq 0 ]"

    try_to_upgrade_dependencies(test_command, depends, pkginfo_path, red, red_depends)


if __name__ == '__main__':
    main()
