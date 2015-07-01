"""
Rename projects to comply with Docker repo restrictions [a-z0-9-_.]
"""
import itertools
import re

import py.path
import yaml

from yaml import safe_load as yaml_load


DOCKER_REPO_CHARS = 'a-z0-9-_.'

DOCKER_REPO_MATCH_RE = re.compile(r'[%s]' % DOCKER_REPO_CHARS)
DOCKER_REPO_SUB_RE = re.compile(r'[^%s]' % DOCKER_REPO_CHARS)

projects_path = py.path.local().join('data', 'projects')
jobs_path = py.path.local().join('data', 'jobs')

all_paths = itertools.chain(projects_path.listdir('*.yaml'),
                            jobs_path.listdir(lambda path: path.isdir()))

for path in all_paths:
    basename = path.basename
    basename = basename.lower()
    basename = DOCKER_REPO_SUB_RE.sub('_', basename)

    new_path = path.dirpath().join(basename)
    path.move(new_path)
