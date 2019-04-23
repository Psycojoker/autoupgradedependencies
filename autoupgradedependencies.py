import os
import sys
import requests

from redbaron import RedBaron


def find_pkginfo(path):
    dirs = os.listdir(path)

    if "__pkginfo__.py" in dirs:
        return os.path.join(path, "__pkginfo__.py")

    cube_subdir = [os.path.join(path, x) for x in dirs if os.path.isdir(x) and x.startswith("cubicweb_")]

    if cube_subdir and "__pkginfo__.py" in os.listdir(cube_subdir[0]):
        return os.path.join(cube_subdir[0], "__pkginfo__.py")

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
            "all_versions": data["releases"].keys()
        }

    return new_depends


def main():
    path = "."
    path = os.path.realpath(os.path.expanduser(path))

    pkginfo_path = find_pkginfo(path)
    print("Foudn __pkginfo__.py: %s" % pkginfo_path)

    depends = parse_pkginfo(pkginfo_path)
    cubes = [x for x in depends if x.startswith("cubicweb-")]

    if cubes:
        sys.stdout.write("Found cubes:\n* ")
        print("\n* ".join(cubes))
    else:
        print("This cube doesn't depends on other cubes")

    depends = merge_depends_with_pypi_info(depends)
    print(depends)


if __name__ == '__main__':
    main()
