"""
Rename "build" models to "job" models
"""
import py.path
import yaml

from yaml import safe_load as yaml_load


builds_path = py.path.local().join('data', 'builds')
jobs_path = py.path.local().join('data', 'jobs')

for project_path in builds_path.listdir():
    build_files = project_path.listdir(
        lambda filename: filename.fnmatch('*.yaml')
    )
    for build_file in build_files:
        with build_file.open() as handle:
            build_data = yaml_load(handle)
        try:
            build_data['job_stage_slugs'] = build_data.pop('build_stage_slugs')
        except KeyError:
            pass
        else:
            with build_file.open('w') as handle:
                yaml.dump(build_data, handle, default_flow_style=False)

builds_path.move(jobs_path)
