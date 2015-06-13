"""
Build metadata stored along side the code
"""

from yaml_model import LoadOnAccess, Model, OnAccess


def get_build(slug):
    """
    Wrapper to import, and return a Build object for the BuildConfig to avoid
    cyclic import
    """
    from dockci.models.build import Build
    return Build(slug)


class BuildConfig(Model):  # pylint:disable=too-few-public-methods
    """
    Build config, loaded from the repo
    """

    slug = 'dockci.yaml'

    build = OnAccess(lambda self: get_build(self.build_slug))
    build_slug = OnAccess(lambda self: self.build.slug)  # TODO infinite loop

    build_output = LoadOnAccess(default=lambda _: {})
    services = LoadOnAccess(default=lambda _: {})

    def __init__(self, build):
        super(BuildConfig, self).__init__()

        assert build is not None, "Build is given"

        self.build = build
        self.build_slug = build.slug

    def data_file_path(self):
        # Our data file path is <build output>/<slug>
        return self.build.build_output_path().join(BuildConfig.slug)
