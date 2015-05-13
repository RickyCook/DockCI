"""
Users and permissions models
"""

from flask.ext.security import UserMixin, RoleMixin

from yaml_model import LoadOnAccess, Model, ValidationError

class User(Model, UserMixin):
    """
    User model for authentication
    """
    slug = None

    email = LoadOnAccess(index=True)
    password = LoadOnAccess(default=lambda _: None)
    active = LoadOnAccess(input_transform=bool, default=False)
    confirmed_at = LoadOnAccess()

    roles = []

    @property
    def id(self):
        return self.slug

    def __init__(self, slug=None, **kwargs):
        if kwargs.get('roles', None):
            raise ValueError("Can's assign roles just yet")

        try:
            del kwargs['roles']

        except KeyError:
            pass

        if slug is None and 'email' in kwargs:
            slug = kwargs['email']

        if not slug:
            raise ValueError("Must give either a slug, or an email")

        self.slug = slug

        super(User, self).__init__()

        for var_name, value in kwargs.items():
            if var_name in self._load_on_access:
                setattr(self, var_name, value)
            else:
                raise AttributeError("Unknown attribute: '%s'" % var_name)


    def validate(self):
        errors = []

        if self.email is None:
            errors.append("Email address is required")

        if errors:
            raise ValidationError(errors)

        return True


class Role(Model, RoleMixin):
    """
    Role model for permissions granting
    """
    slug = None
    description = LoadOnAccess()

    @property
    def id(self):
        return self.slug

    @property
    def name(self):
        return self.slug


    def __init__(self, slug):
        self.slug = slug
        super(User, self).__init__()
