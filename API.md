# Paseetah Real Estate API

**Base URL:** `https://paseetah-api-production.up.railway.app`

---

## Authentication

All endpoints are pre-authenticated via a server-managed session. No API key or token is required on the client side. The server automatically refreshes the session on `401`/`403` and retries once before returning an error.

---

## Endpoints

### Auth

#### `GET /api/v1/refresh-session`
Check whether the current session is alive.

**Response**
```json
{ "alive": true, "message": "Session is healthy." }
```
Returns `401` with `"alive": false` if the session has expired and needs to be renewed server-side.

---

### MOJ — Ministry of Justice

#### `POST /api/v1/fetch-moj`
Fetch sales transactions from the Ministry of Justice dataset.

**Request body**

| Field | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `regions` | integer[] | `[]` | Region IDs |
| `cities` | integer[] | `[]` | City IDs |
| `neighborhoods` | integer[] | `[]` | Neighborhood IDs |
| `deals_exact_match` | boolean | `true` | Exact match on deal numbers |
| `plans_exact_match` | boolean | `true` | Exact match on plan numbers |
| `parcels_exact_match` | boolean | `true` | Exact match on parcel numbers |
| `zoning` | string[] | *(omitted)* | Filter by zoning type. `"1"` = Commercial (تجاري), `"2"` = Residential (سكني) |
| `minAreaSize` | number | *(omitted)* | Minimum plot size in m² (inclusive) |
| `maxAreaSize` | number | *(omitted)* | Maximum plot size in m² (inclusive) |
| `sort_column` | string | `"deal_date"` | Column to sort by |
| `sort_order` | string | `"descending"` | `"ascending"` or `"descending"` |

**Example — residential plots between 600–1000 m²**
```bash
curl -X POST https://paseetah-api-production.up.railway.app/api/v1/fetch-moj \
  -H "Content-Type: application/json" \
  -d '{
    "page": 1,
    "regions": [1],
    "cities": [106],
    "neighborhoods": [111060554],
    "zoning": ["2"],
    "minAreaSize": 600,
    "maxAreaSize": 1000,
    "sort_column": "deal_date",
    "sort_order": "descending"
  }'
```

---

### Civil — Real Estate Register

#### `POST /api/v1/fetch-civil`
Fetch transactions from the Real Estate Register (RER / civil records) dataset.

**Request body**

| Field | Type | Default | Description |
|---|---|---|---|
| `page` | integer | `1` | Page number |
| `regions` | integer[] | `[]` | Region IDs |
| `cities` | integer[] | `[]` | City IDs |
| `neighborhoods` | integer[] | `[]` | Neighborhood IDs |
| `planExactMatch` | boolean | `true` | Exact match on plan numbers |
| `parcelExactMatch` | boolean | `true` | Exact match on parcel numbers |
| `realEstateNumberExactMatch` | boolean | `true` | Exact match on real estate numbers |
| `zoning` | string[] | *(omitted)* | Filter by zoning type. `"1"` = Commercial (تجاري), `"2"` = Residential (سكني) |
| `minAreaSize` | number | *(omitted)* | Minimum plot size in m² (inclusive) |
| `maxAreaSize` | number | *(omitted)* | Maximum plot size in m² (inclusive) |
| `sort_column` | string | `"transaction_date"` | Column to sort by |
| `sort_order` | string | `"descending"` | `"ascending"` or `"descending"` |

**Example — commercial plots only, no size filter**
```bash
curl -X POST https://paseetah-api-production.up.railway.app/api/v1/fetch-civil \
  -H "Content-Type: application/json" \
  -d '{
    "page": 1,
    "regions": [1],
    "cities": [106],
    "neighborhoods": [111060554],
    "zoning": ["1"],
    "sort_column": "transaction_date",
    "sort_order": "descending"
  }'
```

---

### Geo — Reference Data

Use these endpoints to resolve IDs for `regions`, `cities`, and `neighborhoods`.

#### `GET /api/v1/geo/regions`
Returns all regions.

#### `GET /api/v1/geo/cities?region_id={id}`
Returns all cities in a region.

#### `GET /api/v1/geo/neighborhoods?city_id={id}`
Returns all neighborhoods in a city.

#### `GET /api/v1/geo/tree?region_id={id}`
Returns a full city → neighborhood tree for a region.

#### `GET /api/v1/geo/riyadh/neighborhoods`
All neighborhoods in Riyadh Region (`region_id=1`).

#### `GET /api/v1/geo/mecca/neighborhoods`
All neighborhoods in Mecca Region (`region_id=3`), including Jeddah, Taif, and others.

#### `GET /api/v1/geo/jeddah/neighborhoods`
All neighborhoods in Jeddah city only (`city_id=16`).

Each neighborhood object has the shape:
```json
{ "id": 111060554, "name_en": "...", "name_ar": "...", "city_id": 106, "region_id": 1 }
```

---

## Zoning values

| Value | Type |
|---|---|
| `"1"` | Commercial (تجاري) |
| `"2"` | Residential (سكني) |

Pass multiple values to include both types: `"zoning": ["1", "2"]`.
Omit the field entirely to return all zoning types.

---

## Notes

- `zoning`, `minAreaSize`, and `maxAreaSize` are all optional. Omitting them applies no filter on those dimensions.
- The `neighborhoods` field must be spelled exactly that way (not `neighbourhoods`) or the filter is silently ignored by the upstream API.
- Responses are proxied directly from Paseetah — the shape matches whatever Paseetah returns for that dataset.
