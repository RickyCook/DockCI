"""
APIs for external information
"""
import json

from flask import request, Response

from dockci.server import APP
from dockci.views.oauth import oauth_required


@APP.route('/<name>/projects.json')
@oauth_required(['github', 'gitlab'])
def git_projects_list_view(name):
    """
    API for GitHub projects list
    """

    if name == 'github':
        endpoint = 'user/repos'
    elif name == 'gitlab':
        endpoint = 'v3/projects'

    else:
        raise NotImplementedError(
            "Don't know how to list projects for '%s'" % name
        )

    data = OAUTH_APPS[name].get(endpoint, {
        'per_page': request.args.get('per_page', 18),
        'page': request.args.get('page', 1),
    }).data

    if name == 'github':
        data = git_projects_list_filter(data, {
            'full_name',
            'clone_url',
            'hooks_url',
        })
    else:
        data = git_projects_list_filter(data, {
            'name_with_namespace',
            'http_url_to_repo',
            'path_with_namespace',
        })

    return Response(
        json.dumps({'repos': data}),
        mimetype='application/json',
    )


def git_projects_list_filter(data, fields):
    """
    Parse list of dicts, filter dict data, output stripped data for projects
    list
    """
    return [
        {
            key: value
            for key, value in repo.items()
            if key in fields
        } for repo in data
    ]


# @APP.route('/_oauth/<name>/<path:uri>')
def oauth_debug_view(name, uri):
    """
    Debugger for arbitrary OAuth GET requests
    """
    return Response(json.dumps(OAUTH_APPS[name].get(uri).data),
                    mimetype='application/json')
