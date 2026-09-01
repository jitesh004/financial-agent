"""FastAPI dependencies for reading the signed-in user.

The authentication itself already happened in the middleware, which is what
closes the API; these only hand the endpoint the `User` it resolved. Declaring
`user: User = Depends(current_user)` is therefore about wanting the object -
never about whether the route is protected.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from .store import User


def optional_user(request: Request) -> User | None:
    """The signed-in user, or None on one of the public endpoints."""
    return getattr(request.state, "user", None)


def current_user(user: User | None = Depends(optional_user)) -> User:
    """The signed-in user.

    The 401 here is a backstop. Any route reaching it has already passed the
    middleware, so in practice this only fires if a path is added to
    PUBLIC_PATHS and then asks for a user anyway.
    """
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


def onboarded_user(user: User = Depends(current_user)) -> User:
    """A user who has finished the wizard.

    Used only where a half-set-up account would produce something misleading
    rather than merely empty. Most endpoints do not want this: the wizard
    itself has to be able to call /api/profile and the Gmail endpoints, and an
    empty dashboard is an honest answer to a ledger with nothing in it yet.
    """
    if not user.onboarded:
        raise HTTPException(
            409, {"message": "Finish setting up your account first.",
                  "code": "onboarding_incomplete",
                  "step": user.onboarding_step})
    return user
