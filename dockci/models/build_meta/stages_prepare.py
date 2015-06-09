"""
Preparation for the main build stages
"""

import subprocess

from dockci.models.build_meta.config import BuildConfig
from dockci.models.build_meta.stages import BuildStageBase, CommandBuildStage


class WorkdirStage(CommandBuildStage):
    """ Prepare the working directory """

    slug = 'git_prepare'

    def __init__(self, build, workdir):
        super(WorkdirStage, self).__init__(
            build, workdir, (
                ['git', 'clone', build.repo, workdir.strpath],
                ['git',
                 '-c', 'advice.detachedHead=false',
                 'checkout', build.commit
                 ],
            )
        )

    def runnable(self, handle):
        """
        Clone and checkout the build
        """
        result = super(WorkdirStage, self).runnable(handle)

        # check for, and load build config
        build_config_file = self.workdir.join(BuildConfig.slug)
        if build_config_file.check(file=True):
            # pylint:disable=no-member
            self.build.build_config.load(data_file=build_config_file)
            self.build.build_config.save()

        return result


class GitInfoStage(BuildStageBase):
    """ Fill the Build with information obtained from the git repo """

    slug = 'git_info'

    def __init__(self, build, workdir):
        super(GitInfoStage, self).__init__(build)
        self.workdir = workdir

    def runnable(self, handle):
        """
        Execute git to retrieve info
        """
        def run_proc(*args):
            """
            Run, and wait for a process with default args
            """
            proc = subprocess.Popen(args,
                                    stdout=subprocess.PIPE,
                                    stderr=handle,
                                    cwd=self.workdir.strpath,
                                    )
            proc.wait()
            return proc

        largest_returncode = 0
        properties_empty = True

        properties = {
            'Author name': ('git_author_name', '%an'),
            'Author email': ('git_author_email', '%ae'),
            'Committer name': ('git_committer_name', '%cn'),
            'Committer email': ('git_committer_email', '%ce'),
            'Full SHA-1 hash': ('commit', '%H'),
        }
        for display_name, (attr_name, format_string) in properties.items():
            proc = run_proc('git', 'show',
                            '-s',
                            '--format=format:%s' % format_string,
                            'HEAD')

            largest_returncode = max(largest_returncode, proc.returncode)
            value = proc.stdout.read().decode().strip()

            if value != '' and proc.returncode == 0:
                setattr(self.build, attr_name, value)
                properties_empty = False
                handle.write((
                    "%s is %s\n" % (display_name, value)
                ).encode())

        ancestor_build = self.build.job.latest_build_ancestor(
            self.workdir,
            self.build.commit,
        )
        if ancestor_build:
            properties_empty = False
            handle.write((
                "Ancestor build is %s\n" % ancestor_build.slug
            ).encode())
            self.build.ancestor_build = ancestor_build

        if properties_empty:
            handle.write("No information about the git commit could be "
                         "derived\n".encode())

        else:
            self.build.save()

        return proc.returncode


class GitChangesStage(CommandBuildStage):
    """
    Get a list of changes from git between now and the most recently built
    ancestor
    """

    slug = 'git_changes'

    def __init__(self, build, workdir):
        cmd_args = []
        if build.has_value('ancestor_build'):
            revision_range_string = '%s..%s' % (
                build.ancestor_build.commit,  # pylint:disable=no-member
                build.commit,
            )

            cmd_args = [
                'git',
                '-c', 'color.ui=always',
                'log', revision_range_string
            ]
        super(GitChangesStage, self).__init__(build, workdir, cmd_args)

    def runnable(self, handle):
        # TODO fix YAML model to return None rather than an empty model so that
        #      if self.ancestor_build will work
        if self.cmd_args:
            return super(GitChangesStage, self).runnable(handle)

        return True
