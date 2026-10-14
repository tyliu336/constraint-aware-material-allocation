"""Reusable validated identifiers, units, and hashes."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field, StringConstraints

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
RuleIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
    ),
]
SemanticVersion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$",
    ),
]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[0-9a-fA-F]{64}$"),
]
PositiveMillimetres = Annotated[
    Decimal,
    Field(gt=0, max_digits=12, decimal_places=3),
]
NonNegativeMillimetres = Annotated[
    Decimal,
    Field(ge=0, max_digits=12, decimal_places=3),
]
Percentage = Annotated[
    Decimal,
    Field(ge=0, le=100, max_digits=5, decimal_places=2),
]
