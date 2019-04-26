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
