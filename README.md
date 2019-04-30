AutoUpgradeDependencies
=======================

This script is meant to upgrades the dependencies of a CubicWeb cube by parsing
its `__pkginfo__.py` and trying to upgrade each of its dependencies one by one
and running tests in the middle.

The algorithm is the following one:

* find `__pkginfo__.py` either in the root of the project or in `cubicweb_{project_name}`
* parse it, extract the values of `__depends__`
* merge those informations with pypi's one
* only keep the packages that can be upgraded
* for all upgradables cubes:
    * try to upgrade to the latest version
    * check if the cube has changed to a new-style cube
    * if so update the imports
    * run tests (a command provided by the user)
        * if the tests successed, commit
        * else, redo the previous step but next upgradable version by next upgradable version until you find the first buggy one, in the case the previous one is the good one, commit it
* redo the same operations for dependencies that aren't cube without the upgrade part
* display of summary of what has been done and which upgrades failed and point to their tests logs
* exit

Install
=======

    pip install --user git+https://github.com/Psycojoker/autoupgradedependencies

Usage
=====

In the folder where the `.hg` is in a classic cube.

    autoupgradedependencies "test command"

Examples:

    autoupgradedependencies "tox -e py27 --recreate"
    autoupgradedependencies "py.test tests"
