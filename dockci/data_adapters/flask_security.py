"""
Adapter for Flask-Security to YAML model
"""

from flask_security.datastore import Datastore, UserDatastore

from dockci.models.auth import Role, User


# TODO remove
# pylint:disable=abstract-method
class YAMLModelDataStore(Datastore):  # pylint:disable=too-few-public-methods
    """
    Generic Flask security Datastore implementation for YAML model
    """
    def __init__(self):
        super(YAMLModelDataStore, self).__init__(None)

    def put(self, model):  # pylint:disable=no-self-use
        """ Store the model """
        model.save()
        return model

    # TODO
    # def delete(self, model):
    #     pass


class YAMLModelUserDataStore(YAMLModelDataStore, UserDatastore):
    """
    Flask security UserDatastore implementation for YAML model
    """
    user_model = User
    role_model = Role

    def get_user(self, id_or_email):
        """ Returns a user matching the specified ID or email address """
        user = self.user_model(id_or_email)
        if user.exists():
            return user

        user = self.user_model.get_where('email', id_or_email)
        try:
            return next(user)

        except StopIteration:
            pass

    def find_user(self, **kwargs):
        """ Returns a user matching the provided parameters """
        return self._find(self.user_model, **kwargs)

    def find_role(self, **kwargs):
        """ Returns a role matching the provided name """
        return self._find(self.role_model, **kwargs)

    def _find(self, model, **kwargs):  # pylint:disable=no-self-use
        """ Return a model matching the given args """
        if len(kwargs) > 1:
            raise ValueError("Can't filter on > 1 field at a time")

        for key, value in kwargs.items():
            return next(model.get_where(key, value))
