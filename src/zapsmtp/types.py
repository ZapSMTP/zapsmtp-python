"""Every request object (TypedDict) and response model of the API.

    from zapsmtp.types import Email, OutgoingAttachment, SendEmailParams
"""

from ._generated.models import *  # noqa: F401,F403
from ._generated.models import __all__ as _models
from ._generated.params import *  # noqa: F401,F403
from ._generated.params import __all__ as _params

__all__ = [*_params, *_models]
