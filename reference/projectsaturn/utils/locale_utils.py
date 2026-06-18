"""Locale normalization utilities for API payloads.

The Ampeco API requires exact locale keys (case-sensitive).
This module provides a function to normalize locale values from column names
to canonical API locale keys.
"""

# Canonical API locale keys (case-sensitive)
LOCALE_MAPPINGS = {
    # English
    "en": "en",
    # Norwegian
    "no": "nb-NO",
    "nb": "nb-NO",
    "nb-no": "nb-NO",
    "nb-NO": "nb-NO",
    # Swedish
    "se": "sv-SE",
    "sv": "sv-SE",
    "sv-se": "sv-SE",
    "sv-SE": "sv-SE",
}


def normalize_locale(value: str) -> str:
    """Normalize a locale value to the canonical API locale key.

    Args:
        value: The locale value to normalize (e.g., 'no', 'nb-NO', 'sv-SE', 'en')

    Returns:
        The canonical API locale key (e.g., 'nb-NO', 'sv-SE', 'en')

    Raises:
        ValueError: If the locale value cannot be mapped to a known locale

    Examples:
        >>> normalize_locale('no')
        'nb-NO'
        >>> normalize_locale('nb-NO')
        'nb-NO'
        >>> normalize_locale('sv')
        'sv-SE'
        >>> normalize_locale('en')
        'en'
    """
    canonical = LOCALE_MAPPINGS.get(value)
    if canonical is None:
        raise ValueError(
            f"Unknown locale '{value}'. "
            f"Known locales: {', '.join(sorted(set(LOCALE_MAPPINGS.values())))}"
        )
    return canonical
