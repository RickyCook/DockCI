"""
Functions for setting up and starting the DockCI application server
"""

import logging
import mimetypes

from flask import Flask
from flask_oauthlib.client import OAuth
from flask_security import Security, SQLAlchemyUserDatastore
from flask_mail import Mail
from flask_restful import Api
from flask_sqlalchemy import SQLAlchemy

#from dockci.data_adapters.flask_security import YAMLModelUserDataStore
from dockci.models.config import Config
from dockci.util import setup_templates, tokengetter_for


APP = Flask(__name__)
MAIL = Mail()
CONFIG = Config()
SECURITY = Security()
DB = SQLAlchemy()
OAUTH = OAuth(APP)
API = Api(APP, prefix='/api/v1')

APP.config.model = CONFIG  # For templates


OAUTH_APPS = {}
OAUTH_APPS_SCOPES = {}
OAUTH_APPS_SCOPE_SERIALIZERS = {
    'github': lambda scope: ','.join(sorted(scope.split(','))),
}


def app_init(app_args={}):
    """
    Pre-run app setup
    """
    logger = logging.getLogger('dockci.init')

    logger.info("Loading app config")

    APP.secret_key = CONFIG.secret

    APP.config['MAIL_SERVER'] = CONFIG.mail_server
    APP.config['MAIL_PORT'] = CONFIG.mail_port
    APP.config['MAIL_USE_TLS'] = CONFIG.mail_use_tls
    APP.config['MAIL_USE_SSL'] = CONFIG.mail_use_ssl
    APP.config['MAIL_USERNAME'] = CONFIG.mail_username
    APP.config['MAIL_PASSWORD'] = CONFIG.mail_password
    APP.config['MAIL_DEFAULT_SENDER'] = CONFIG.mail_default_sender

    APP.config['SECURITY_PASSWORD_HASH'] = 'bcrypt'
    APP.config['SECURITY_PASSWORD_SALT'] = CONFIG.security_password_salt
    APP.config['SECURITY_REGISTERABLE'] = CONFIG.security_registerable
    APP.config['SECURITY_RECOVERABLE'] = CONFIG.security_recoverable
    APP.config['SECURITY_CHANGEABLE'] = True
    APP.config['SECURITY_EMAIL_SENDER'] = CONFIG.mail_default_sender

    APP.config['SQLALCHEMY_DATABASE_URI'] = app_args.get(
        'db_uri', 'sqlite:////tmp/data.db'
    )

    mimetypes.add_type('application/x-yaml', 'yaml')

    from dockci.models.auth import User, Role
    from dockci.models.job import Job
    from dockci.models.project import Project
    SECURITY.init_app(APP, SQLAlchemyUserDatastore(DB, User, Role))
    MAIL.init_app(APP)
    DB.init_app(APP)
    app_init_oauth()
    app_init_api()
    app_init_views()


def app_init_oauth():
    """
    Initialize the OAuth integrations
    """
    if CONFIG.github_key and CONFIG.github_secret:
        scope = 'user:email,admin:repo_hook,repo'
        OAUTH_APPS_SCOPES['github'] = \
            OAUTH_APPS_SCOPE_SERIALIZERS['github'](scope)
        OAUTH_APPS['github'] = OAUTH.remote_app(
            'github',
            consumer_key=CONFIG.github_key,
            consumer_secret=CONFIG.github_secret,
            request_token_params={'scope': scope},
            base_url='https://api.github.com/',
            request_token_url=None,
            access_token_method='POST',
            access_token_url='https://github.com/login/oauth/access_token',
            authorize_url='https://github.com/login/oauth/authorize'
        )

    for oauth_app in OAUTH_APPS.values():
        oauth_app.tokengetter(tokengetter_for(oauth_app))


def app_init_api():
    """ Activate the DockCI API """
    import dockci.api.project
    import dockci.api.user


def app_init_views():
    """
    Activate all DockCI views
    """
    # pylint:disable=unused-variable
    import dockci.views.core

    import dockci.views.job
    import dockci.views.external
    import dockci.views.project
    import dockci.views.oauth
    import dockci.views.test

    setup_templates(APP)


def run(app_args):
    """
    Setup, and run the DockCI application server, using the args given to
    configure it
    """
    app_init(app_args)
    server_args = {
        key: val
        for key, val in app_args.items()
        if key in ('host', 'port', 'debug')
    }

    APP.run(**server_args)
