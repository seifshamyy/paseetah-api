from pydantic import BaseModel


class PaseetahDataRequest(BaseModel):
    page: int = 1
    regions: list[int] = []
    cities: list[int] = []
    deals_exact_match: bool = True
    plans_exact_match: bool = True
    parcels_exact_match: bool = True
    sort_column: str = "deal_date"
    sort_order: str = "descending"
