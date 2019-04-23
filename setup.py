#!/usr/bin/python
# -*- coding:Utf-8 -*-

from setuptools import setup

setup(name='autoupgradedependencies',
      version='0.1',
      description='auto upgrade a cubicweb cube dependencies',
      author='Laurent Peuch',
      # long_description='',
      author_email='cortex@worlddomination.be',
      url='https://github.com/Psycojoker/autoupgradedependencies',
      install_requires=["redbaron"],
      py_modules=['autoupgradedependencies'],
      license='gplv3+',
      entry_points={
          'console_scripts': [
              'autoupgradedependencies = autoupgradedependencies:main'
          ]
      },
      keywords='',
      )
