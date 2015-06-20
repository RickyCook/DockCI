"""
Rename "job" models to "project" models
"""
import py.path


jobs_path = py.path.local().join('data', 'jobs')
projects_path = py.path.local().join('data', 'projects')

jobs_path.move(projects_path)
