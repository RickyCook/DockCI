"""
APIs for external information
"""
import json

from flask_security import login_required

from dockci.server import APP, OAUTH_APPS
from dockci.views.oauth import oauth_required


@APP.route('/<name>/projects.json')
@login_required
@oauth_required(['github'])
def git_projects_list_view(name):
    """
    API for GitHub projects list
    """
    data = OAUTH_APPS[name].get('user/repos').data
    data = {'repos': [
        {
            key: value
            for key, value in repo.items()
            if key in {'full_name', 'clone_url', 'hooks_url'}
        } for repo in data
    ]}
    return json.dumps(data)


# @APP.route('/_oauth/<name>/<path:uri>')
def oauth_debug_view(name, uri):
    """
    Debugger for arbitrary OAuth GET requests
    """
    from flask import Response
    return Response(json.dumps(OAUTH_APPS[name].get(uri).data),
                    mimetype='application/json')
