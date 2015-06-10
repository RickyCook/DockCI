"""
Core app views
"""

import py.error  # pylint:disable=import-error

from flask import render_template, request
from flask_security import login_required

from dockci.models.project import all_projects
from dockci.server import APP, CONFIG
from dockci.util import request_fill


@APP.route('/')
def index_view():
    """
    View to display the list of all projects
    """
    return render_template('index.html', projects=list(all_projects()))


@APP.route('/config', methods=('GET', 'POST'))
@login_required
def config_edit_view():
    """
    View to edit global config
    """
    restart_fields = (
        'secret',
        'docker_use_env_vars', 'docker_hosts',
        'mail_host_string', 'mail_use_tls', 'mail_use_ssl',
        'mail_username', 'mail_password', 'mail_default_sender',
        'security_registerable', 'security_recoverable',
        'github_key', 'github_secret',
    )
    all_fields = restart_fields + (
        'docker_use_registry', 'docker_registry',
    )

    saved = request_fill(CONFIG, all_fields)

    try:
        CONFIG.load()

    except py.error.ENOENT:
        pass

    if saved:
        restart_needed = any((
            (
                attr in request.form and
                request.form[attr] != getattr(CONFIG, attr)
            )
            for attr in restart_fields
        ))
        if restart_needed:
            CONFIG.restart_needed = True

    return render_template('config_edit.html')
