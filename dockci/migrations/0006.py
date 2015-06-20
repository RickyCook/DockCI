"""
Remame "error" status to "broken"
"""
import py.path
import yaml

from yaml import safe_load as yaml_load


jobs_path = py.path.local().join('data', 'jobs')

for project_path in jobs_path.listdir():
    build_files = project_path.listdir(
        lambda filename: filename.fnmatch('*.yaml')
    )
    for build_file in build_files:
        with build_file.open() as handle:
            build_data = yaml_load(handle)

        if build_data.get('result', None) == 'error':
            build_data['result'] = 'broken'
            with build_file.open('w') as handle:
                yaml.dump(build_data, handle, default_flow_style=False)
