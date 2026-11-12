"""Exceptions whose message can be shown to the user as is."""


class UserError(Exception):
    """A problem the user can understand or fix, e.g. "Today's YouTube quota is used up".

    The message is plain language for the UI. Technical details belong in the log.
    """
