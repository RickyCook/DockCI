"""
Views related to project management
"""

import re

from flask import abort, flash, redirect, render_template, request, url_for
from flask_security import current_user, login_required

from dockci.models.project import Project
from dockci.server import APP, CONFIG
from dockci.util import (auth_token_data,
                         auth_token_expiry,
                         create_auth_token,
                         model_flash,
                         request_fill,
                         validate_auth_token,
                         )


@APP.route('/projects/<slug>', methods=('GET', 'POST'))
def project_view(slug):
    """
    View to display a project
    """
    project = Project(slug)
    if not project.exists():
        abort(404)

    if request.method == 'POST':
        if request.form.get('operation', None) == 'delete':
            auth_token_okay = validate_auth_token(
                CONFIG.secret, request.form, current_user, project,
            )
            if auth_token_okay:
                project.delete()
                if project.exists():
                    flash("Unexpected issue deleting '%s'" % project.slug,
                          'danger')
                    return redirect(url_for('project_view', slug=project.slug))
                else:
                    flash("Deleted '%s'" % project.slug, 'success')
                    return redirect('/')
            else:
                flash("Authentication token mismatch", 'danger')
                return redirect(url_for('project_view', slug=project.slug))

    else:
        page_size = int(request.args.get('page_size', 20))
        page_offset = int(request.args.get('page_offset', 0))
        versioned = 'versioned' in request.args

        if versioned:
            jobs = list(project.filtered_jobs(passed=True, versioned=True))
        else:
            jobs = project.jobs

        prev_page_offset = max(page_offset - page_size, 0)
        if page_offset < 1:
            prev_page_offset = None

        next_page_offset = page_offset + page_size
        if next_page_offset > len(jobs):
            next_page_offset = None

        auth_token_expires = auth_token_expiry()
        auth_token_delete = {
            'auth_token': create_auth_token(
                CONFIG.secret, auth_token_data(
                    current_user, project, 'delete', auth_token_expires
                ),
            ),
            'expiry': auth_token_expires,
        }

        jobs = jobs[page_offset:page_offset + page_size]
        return render_template('project.html',
                               project=project,
                               jobs=jobs,
                               versioned=versioned,
                               prev_page_offset=prev_page_offset,
                               next_page_offset=next_page_offset,
                               page_size=page_size,
                               auth_token_delete=auth_token_delete,
                               )


@APP.route('/projects/<slug>/edit', methods=('GET', 'POST'))
@login_required
def project_edit_view(slug):
    """
    View to edit a project
    """
    project = Project(slug)
    if not project.exists():
        abort(404)

    return project_input_view(project, 'edit', [
        'name', 'repo', 'github_secret',
        'hipchat_api_token', 'hipchat_room',
    ])


@APP.route('/projects/new', methods=('GET', 'POST'))
@login_required
def project_new_view():
    """
    View to make a new project
    """
    project = Project()
    return project_input_view(project, 'new', [
        'slug', 'name', 'repo', 'github_secret', 'github_repo_id',
        'hipchat_api_token', 'hipchat_room',
    ])


def handle_github_hook(project):
    """ Try to add a GitHub hook for a project """
    if model_flash(project, save=False):
        result = project.add_github_webhook()  # auto saves on success

        if result.status == 201:
            return True

        else:
            flash(result.data.get(
                'message',
                ("Unexpected response from GitHub. "
                 "HTTP status %d") % result.status
            ), 'danger')

    return False


def project_input_view(project, edit_operation, fields):
    """ Generic view for project editing """
    if request.method == 'POST':
        fill_data = request.form.to_dict()

        # Filter out github properties if not a github repo, so that they are
        # unset on the project
        if request.args.get('repo_type', None) == 'github':
            fill_data['github_auth_user'] = current_user
            fields.append('github_auth_user')
        else:
            fill_data['github_repo_id'] = None
            fields.append('github_repo_id')

        save = request.args.get('repo_type', None) != 'github'
        save &= edit_operation != 'new'

        saved = request_fill(
            project, fields,
            data=fill_data,
            save=save,
        )

        if edit_operation == 'new':
            if project.exists():
                flash("Project with slug '%s' already exists" % project.slug,
                      'danger')
                saved = False

            elif request.args.get('repo_type', None) == 'github':
                saved = handle_github_hook(project)

            else:
                project.save()

        elif request.args.get('repo_type', None) == 'github':
            saved = handle_github_hook(project)

        if saved:
            return redirect(
                '/projects/{project_slug}'.format(project_slug=project.slug)
            )

    if 'repo_type' in request.args:
        default_repo_type = request.args['repo_type']

    elif current_user is None or not current_user.is_authenticated():
        default_repo_type = 'manual'

    elif 'github' in current_user.oauth_tokens:
        default_repo_type = 'github'

    else:
        default_repo_type = 'manual'

    re.sub(r'[^\w\s]', '', default_repo_type)

    return render_template('project_edit.html',
                           project=project,
                           edit_operation=edit_operation,
                           default_repo_type=default_repo_type,
                           )
