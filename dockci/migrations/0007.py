"""
Fix artifacts after rename from build models to job models
"""
import py.error
import py.path
import yaml

from yaml import safe_load as yaml_load


jobs_path = py.path.local().join('data', 'jobs')

for project_path in jobs_path.listdir():
    build_files = project_path.listdir(
        lambda filename: filename.fnmatch('*.yaml')
    )
    for build_file in build_files:
        build_slug = build_file.purebasename
        build_config_file = project_path.join(
            '%s_output' % build_slug, 'dockci.yaml',
        )
        try:
            with build_config_file.open() as handle:
                build_config_data = yaml_load(handle)

            if 'build_output' in build_config_data:
                build_config_data['job_output'] = \
                    build_config_data.pop('build_output')
                with build_config_file.open('w') as handle:
                    yaml.dump(build_config_data, handle, default_flow_style=False)

        except py.error.ENOENT:
            pass
