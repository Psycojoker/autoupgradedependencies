# encoding: utf-8

import os
import re
import sys
import operator
import itertools

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
    return eval(depends.value.dumps())


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
        possible_upgrades = list(itertools.dropwhile(lambda x: x < maximum_version, all_versions_sorted))

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


def main():
    path = "."
    path = os.path.realpath(os.path.expanduser(path))

    pkginfo_path = find_pkginfo(path)
    print("Foudn __pkginfo__.py: %s" % pkginfo_path)

    depends = parse_pkginfo(pkginfo_path)
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
    print(depends)


if __name__ == '__main__':
    main()
