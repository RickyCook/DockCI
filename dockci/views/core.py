"""
Core app views
"""

from flask import render_template, request

from dockci.models.job import all_jobs
from dockci.server import APP, CONFIG
from dockci.util import request_fill


@APP.route('/')
def root_view():
    """
    View to display the list of all jobs
    """
    return render_template('index.html', jobs=list(all_jobs()))


@APP.route('/config', methods=('GET', 'POST'))
def config_edit_view():
    """
    View to edit global config
    """
    restart_fields = (
        'secret',
        'docker_use_env_vars', 'docker_host',
        'mail_host_string', 'mail_use_tls',
        'mail_use_ssl', 'mail_username', 'mail_password', 'mail_default_sender'
    )
    all_fields = restart_fields + (
        'docker_use_registry', 'docker_registry',
    )

    saved = request_fill(CONFIG, all_fields)
    CONFIG.load()

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
