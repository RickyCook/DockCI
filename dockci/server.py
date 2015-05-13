"""
Functions for setting up and starting the DockCI application server
"""

import logging
import mimetypes

from flask import Flask
from flask.ext.security import Security
from flask_mail import Mail

from dockci.data_adapters.flask_security import YAMLModelUserDataStore
from dockci.models.config import Config
from dockci.util import setup_templates


APP = Flask(__name__)
MAIL = Mail()
CONFIG = Config()
SECURITY = Security()

APP.config.model = CONFIG  # For templates


def app_init():
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

    mimetypes.add_type('application/x-yaml', 'yaml')

    SECURITY.init_app(APP, YAMLModelUserDataStore())
    MAIL.init_app(APP)
    app_init_views()


def app_init_views():
    """
    Activate all DockCI views
    """
    # pylint:disable=unused-variable
    import dockci.views.core

    import dockci.views.build
    import dockci.views.job

    setup_templates(APP)


def run(app_args):
    """
    Setup, and run the DockCI application server, using the args given to
    configure it
    """
    app_init()
    server_args = {
        key: val
        for key, val in app_args.items()
        if key in ('host', 'port', 'debug')
    }

    APP.run(**server_args)
