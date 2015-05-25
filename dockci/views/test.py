"""
Configuration status tests
"""

import json

import requests

from flask import abort, request
from flask_security import login_required
from requests.exceptions import RequestException

from dockci.server import APP, CONFIG


@APP.route('/test/<part>.json')
@login_required
def test_view(part):
    """
    Run tests on a variety of config values
    """
    if part == 'registry':
        results = RegistryTests(request.args.get('url', None)).all_tests()

    else:
        return abort(404)

    return json.dumps(results)


class Tester(object):  # pylint:disable=too-few-public-methods
    """
    Base class for running generic tests against config
    """
    def all_tests(self):
        """
        Run all methods on this object that end in _test, returning results
        """
        data = {
            test: getattr(self, '%s_test' % test)()
            for test in [
                fn_name[:-5]
                for fn_name in dir(self)
                if fn_name[-5:] == '_test'
            ]
        }
        return {
            test_name: {'result': test_result, 'detail': test_detail}
            for test_name, (test_result, test_detail) in data.items()
        }


class RegistryTests(Tester):  # pylint:disable=too-few-public-methods
    """
    Class to run tests against a registry URL
    """
    def __init__(self, url=None):
        if url is None:
            url = CONFIG.docker_registry

        self.url = url

    def api_test(self):
        """
        Basic test of the registry v2 endpoint
        """
        try:
            resp = requests.get('%s/v2/' % self.url)

        except RequestException as ex:
            return False, "{exname}: {message}".format(
                exname=ex.__class__.__name__, message=ex,
            )

        if resp.status_code != 200:
            return False, "HTTP %s: %s" % (resp.status_code, resp.reason)

        return True, ''
