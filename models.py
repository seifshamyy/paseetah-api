from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class MojDataRequest(BaseModel):
    """Ministry of Justice — sales_transaction endpoint."""
    page: int = 1
    regions: list[int] = []
    cities: list[int] = []
    neighborhoods: list[int] = []
    deals_exact_match: bool = True
    plans_exact_match: bool = True
    parcels_exact_match: bool = True
    zoning: Optional[list[str]] = None
    minAreaSize: Optional[float] = None
    maxAreaSize: Optional[float] = None
    sort_column: str = "deal_date"
    sort_order: str = "descending"


class CivilDataRequest(BaseModel):
    """Civil / Real-Estate Register — rer_transactions endpoint."""
    page: int = 1
    regions: list[int] = []
    cities: list[int] = []
    neighborhoods: list[int] = []
    planExactMatch: bool = True
    parcelExactMatch: bool = True
    realEstateNumberExactMatch: bool = True
    zoning: Optional[list[str]] = None
    minAreaSize: Optional[float] = None
    maxAreaSize: Optional[float] = None
    sort_column: str = "transaction_date"
    sort_order: str = "descending"


# Backwards-compat alias
PaseetahDataRequest = MojDataRequest
