import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class PasswordComplexityValidator:
    """
    Requires at least one uppercase letter, one lowercase letter,
    one digit, and one special character.
    Used alongside MinimumLengthValidator in AUTH_PASSWORD_VALIDATORS.
    """

    _SPECIAL = r'[!@#$%^&*()\-_=+\[\]{}|;:\'",.<>?/`~\\]'

    def validate(self, password, user=None):
        missing = []
        if not re.search(r'[A-Z]', password):
            missing.append(_("an uppercase letter (A–Z)"))
        if not re.search(r'[a-z]', password):
            missing.append(_("a lowercase letter (a–z)"))
        if not re.search(r'\d', password):
            missing.append(_("a digit (0–9)"))
        if not re.search(self._SPECIAL, password):
            missing.append(_("a special character (!@#$%^&* …)"))
        if missing:
            raise ValidationError(
                _("Password must also include: %(req)s."),
                code='password_too_weak',
                params={'req': ', '.join(missing)},
            )

    def get_help_text(self):
        return _(
            "Your password must contain at least one uppercase letter, "
            "one lowercase letter, one digit, and one special character."
        )
