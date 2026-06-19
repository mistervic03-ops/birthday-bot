from __future__ import annotations


def slack_error_reason(error: Exception) -> str:
    response = getattr(error, "response", None)
    if response is not None:
        try:
            slack_error = response.get("error")
        except AttributeError:
            slack_error = None
        if slack_error:
            return str(slack_error)

        data = getattr(response, "data", None)
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])

    return str(error) or error.__class__.__name__
